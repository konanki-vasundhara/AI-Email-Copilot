"""Hugging Face AI Service for Email Analysis, Summarization, and Task Extraction."""
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import httpx
from app.config import settings
from app.db.models import Email, EmailAnalysis, Task, ReplyDraft
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class AIService:
    MODEL_CLASSIFIER = settings.HF_MODEL_CLASSIFIER
    MODEL_SUMMARIZER = settings.HF_MODEL_SUMMARIZER

    CATEGORIES = [
        "Work",
        "Action Required",
        "Personal",
        "Financial / Billing",
        "Newsletter / Marketing",
    ]

    URGENT_KEYWORDS = [
        "urgent", "asap", "deadline", "immediately", "action required",
        "due by", "critical", "high priority", "attention needed", "eod", "end of day"
    ]

    BILLING_KEYWORDS = [
        "invoice", "receipt", "payment", "billing", "subscription",
        "charge", "amount due", "statement", "order confirmation"
    ]

    NEWSLETTER_KEYWORDS = [
        "unsubscribe", "view in browser", "weekly digest", "newsletter",
        "promotions", "special offer", "discount", "edition", "updates"
    ]

    async def call_hf_summarizer(self, text: str) -> Optional[str]:
        """Calls Hugging Face Inference API for BART summarization."""
        token = settings.HF_API_TOKEN
        clean = text.strip()
        if not token or len(clean) < 40:
            return None
        url = f"https://api-inference.huggingface.co/models/{self.MODEL_SUMMARIZER}"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"inputs": clean[:1024], "parameters": {"max_length": 130, "min_length": 25}}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, list) and len(data) > 0 and "summary_text" in data[0]:
                        return data[0]["summary_text"].strip()
        except Exception:
            pass
        return None

    async def call_hf_classifier(self, text: str) -> Optional[Dict[str, Any]]:
        """Calls Hugging Face Zero-Shot classification via BART-large-mnli."""
        token = settings.HF_API_TOKEN
        clean = text.strip()
        if not token or len(clean) < 15:
            return None
        url = f"https://api-inference.huggingface.co/models/{self.MODEL_CLASSIFIER}"
        headers = {"Authorization": f"Bearer {token}"}
        candidate_labels = ["Work", "Action Required", "Personal", "Financial / Billing", "Newsletter / Marketing"]
        payload = {"inputs": clean[:512], "parameters": {"candidate_labels": candidate_labels}}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    labels = data.get("labels", [])
                    scores = data.get("scores", [])
                    if labels and scores:
                        top_label = labels[0]
                        importance = "High" if top_label == "Action Required" else "Normal"
                        return {
                            "category": top_label,
                            "importance": importance,
                            "sentiment": "Neutral",
                            "model_id": self.MODEL_CLASSIFIER,
                        }
        except Exception:
            pass
        return None

    def sanitize_untrusted_input(self, text: str, max_chars: int = 4000) -> str:
        """
        Guards against prompt injection by treating email text as untrusted.
        Normalizes whitespace and bounds length.
        """
        if not text:
            return ""
        # Strip potential prompt injection control tokens
        cleaned = re.sub(r"(?i)(ignore (all )?previous instructions|system:\s*|human:\s*)", "[redacted]", text)
        return cleaned.strip()[:max_chars]

    def classify_email(self, subject: str, body: str) -> Dict[str, Any]:
        """Classifies email into category and importance level."""
        combined = f"{subject} {body}".lower()

        # 1. Detect Category
        if any(w in combined for w in self.NEWSLETTER_KEYWORDS):
            category = "Newsletter / Marketing"
            importance = "Low"
        elif any(w in combined for w in self.BILLING_KEYWORDS):
            category = "Financial / Billing"
            importance = "Normal"
        elif any(w in combined for w in self.URGENT_KEYWORDS):
            category = "Action Required"
            importance = "High"
        elif any(w in combined for w in ["meeting", "project", "review", "contract", "client", "team", "sync", "sprint"]):
            category = "Work"
            importance = "High" if any(w in combined for w in self.URGENT_KEYWORDS) else "Normal"
        else:
            category = "Work"
            importance = "Normal"

        # Sentiment / tone indicator
        sentiment = "Neutral"
        if any(w in combined for w in ["thank you", "great work", "appreciate", "congratulations"]):
            sentiment = "Positive"
        elif any(w in combined for w in ["urgent", "asap", "issue", "blocked", "failed", "error", "delay"]):
            sentiment = "Urgent"

        return {
            "category": category,
            "importance": importance,
            "sentiment": sentiment,
            "model_id": self.MODEL_CLASSIFIER,
        }

    def clean_text_for_summary(self, text: str) -> str:
        """Strips long URLs, tracking tokens, and formatting artifacts for clean human-readable summaries."""
        if not text:
            return ""
        # Strip URLs
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"www\.\S+", "", text)
        # Strip repeated dots, dashes, and underscores
        text = re.sub(r"[\.]{2,}", " ", text)
        text = re.sub(r"[\-_=]{2,}", " ", text)
        # Remove common marketing/digest footer clutter
        text = re.sub(r"(?i)\b(unsubscribe|view in browser|view message|view invitation|manage preferences)\b.*", "", text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def summarize_email(self, subject: str, body: str) -> str:
        """Generates a concise executive summary of the email."""
        clean_body = self.clean_text_for_summary(self.sanitize_untrusted_input(body))
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean_body) if len(s.strip()) > 15]

        if not sentences:
            clean_sub = self.clean_text_for_summary(subject)
            return f"Regarding: {clean_sub}" if clean_sub else "No summary available."

        if len(sentences) <= 2:
            summary = " ".join(sentences)
        else:
            summary = f"{sentences[0]} {sentences[1]}"

        return summary[:400]

    def is_junk_task_title(self, text: str) -> bool:
        """
        Determines whether a sentence is boilerplate, system messages, or unsubscription
        disclaimers rather than an actionable task.
        """
        lower = text.lower()
        junk_patterns = [
            r"nothing (that )?you need to do",
            r"no action (is )?(needed|required)",
            r"don('t|ot) need to do anything",
            r"enable javascript",
            r"javascript is (disabled|required)",
            r"(kindly|please)\s+(ignore|disregard)\s+this\s+email",
            r"no longer wish to receive",
            r"reply with ['\"]?remove['\"]?",
            r"\bunsubscribe\b",
            r"\bopt[- ]out\b",
            r"manage\s+(your\s+)?preferences",
            r"view (this )?email in (your )?browser",
            r"view (online|invitation|message|profile|thread)",
            r"^\(?(we only send|if unanswered)",
            r"^\*?contact:?\s*\*?please write",
            r"do not reply to this (email|message)",
            r"automated (message|notification|email)",
            r"this email was (sent|intended) for",
            r"if you have received this (email|message) in error",
            r"terms (of service|and conditions)",
            r"privacy policy",
            r"all rights reserved",
            r"click here to",
            r"^to unsubscribe",
            r"having trouble viewing this",
        ]
        return any(re.search(pat, lower) for pat in junk_patterns)

    def parse_due_date(self, text: str, base_time: datetime) -> Optional[datetime]:
        """Parses target deadlines from natural language phrases."""
        lower = text.lower()

        def parse_time_component(t_str: Optional[str]) -> tuple[int, int]:
            if not t_str:
                return 18, 0
            m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t_str.strip().lower())
            if not m:
                return 18, 0
            hr = int(m.group(1))
            mn = int(m.group(2) or 0)
            mer = m.group(3)
            if mer == "pm" and hr < 12:
                hr += 12
            elif mer == "am" and hr == 12:
                hr = 0
            return hr, mn

        # 1. Matches DD-MM-YYYY or DD/MM/YYYY with optional time
        # e.g., 'before 1 PM 31-10-2026' or 'by 31/10/2026' or 'due 15-11-2026'
        m_num = re.search(
            r"(?:before|by|until|due|at)?\s*(?:(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+)?(?:on\s+)?(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})",
            lower,
        )
        if m_num and (m_num.group(1) or any(w in lower for w in ["before", "by", "until", "due", "deadline"])):
            t_str, d, m, y = m_num.groups()
            hr, mn = parse_time_component(t_str)
            try:
                return datetime(int(y), int(m), int(d), hr, mn, tzinfo=timezone.utc)
            except Exception:
                pass

        # 2. Check for explicit time mentioned with today / eod / tomorrow
        hr, mn = 18, 0
        t_match = re.search(r"\b(?:at|by|before)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", lower)
        if t_match:
            hr, mn = parse_time_component(t_match.group(1))

        # Immediate / today / eod / cob
        if any(w in lower for w in ["today", "eod", "end of day", "cob", "close of business"]):
            return base_time.replace(hour=hr, minute=mn, second=0, microsecond=0)

        # Tomorrow
        if "tomorrow" in lower:
            t = base_time + timedelta(days=1)
            return t.replace(hour=hr, minute=mn, second=0, microsecond=0)

        # Days of the week
        days_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        for day_name, target_weekday in days_map.items():
            if re.search(rf"\b(by|before|on|until)?\s*{day_name}\b", lower):
                current_weekday = base_time.weekday()
                days_ahead = (target_weekday - current_weekday) % 7
                if days_ahead == 0:
                    days_ahead = 7
                t = base_time + timedelta(days=days_ahead)
                return t.replace(hour=hr, minute=mn, second=0, microsecond=0)

        # Explicit month & day (e.g. by Oct 31, by November 15)
        month_names = {
            "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
            "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
            "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
            "nov": 11, "november": 11, "dec": 12, "december": 12,
        }
        m_match = re.search(
            r"\b(?:by|before|due|deadline(?:\s+is)?)\s+([a-zA-Z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b",
            lower,
        )
        if m_match:
            m_str, d_str = m_match.group(1), m_match.group(2)
            if m_str in month_names:
                try:
                    month = month_names[m_str]
                    day = int(d_str)
                    year = base_time.year
                    target = datetime(year, month, day, hr, mn, 0, tzinfo=timezone.utc)
                    if target < base_time:
                        target = datetime(year + 1, month, day, hr, mn, 0, tzinfo=timezone.utc)
                    return target
                except Exception:
                    pass

        return None

    def extract_tasks_and_deadlines(
        self,
        subject: str,
        body: str,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Extracts action items, requests, and target deadlines from email content.
        Skips marketing and newsletter emails to prevent noise.
        """
        # Marketing & newsletters rarely contain legitimate personal action tasks
        if category and "marketing" in category.lower():
            return []

        # Extract tasks from body content; fallback to subject if body is empty
        source_text = body.strip() if body and body.strip() else subject.strip()
        clean_text = self.sanitize_untrusted_input(source_text)
        sentences = re.split(r"(?<=[.!?\n])\s+", clean_text)

        action_patterns = [
            r"(?i)\b(please|kindly|could you|can you|need to|must|ensure to|review|prepare|send|submit|approve|schedule|register|complete|fill out|verify|confirm|share|provide|forward|upload|sign|check|reach out|let me know|let us know)\b.*",
            r"(?i)\b(reply(\s+to|\s+back|\s+with)?|respond(\s+to)?|apply|applying|interested in)\b.*",
            r"(?i)\b(deadline\s+(?:is|for|to)|due\s+(?:by|on|date)|due:)\b.*",
        ]

        extracted_tasks: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for s in sentences:
            s_clean = s.strip()
            # Clean common bullet or quote marks
            s_clean = re.sub(r"^[\*\-\•\(\[\"\'\s]+", "", s_clean)
            s_clean = re.sub(r"[\*\-\•\)\]\"\'\s]+$", "", s_clean).strip()

            # Clean greetings like "Hii vassu, ", "Hi John, ", "Hello,"
            s_clean = re.sub(r"^(?:h(?:i+|ello|ey)|dear)\s+[a-zA-Z\s]+,\s*", "", s_clean, flags=re.IGNORECASE).strip()

            if len(s_clean) < 12 or len(s_clean) > 220:
                continue

            if self.is_junk_task_title(s_clean):
                continue

            for pattern in action_patterns:
                match = re.search(pattern, s_clean)
                if match:
                    due_at = self.parse_due_date(s_clean, now)
                    # Capitalize first letter cleanly
                    title = s_clean.rstrip(".")
                    if title:
                        title = title[0].upper() + title[1:]
                    if not any(t["title"] == title for t in extracted_tasks):
                        extracted_tasks.append({
                            "title": title,
                            "due_at": due_at,
                        })
                    break

        return extracted_tasks

    def generate_reply_draft(
        self,
        subject: str,
        sender: str,
        body: str,
        tasks: List[Dict[str, Any]],
        user_name: Optional[str] = None,
    ) -> str:
        """
        Generates a context-aware, highly personalized professional draft reply.
        Strict safeguard: This is a suggested draft only and will NEVER be sent without user confirmation.
        """
        # Determine sender name cleanly
        sender_clean = sender.split("<")[0].replace('"', '').replace("'", "").strip()
        sender_first = sender_clean.split()[0].capitalize() if sender_clean else ""
        salutation = f"Hi {sender_first}," if sender_first else "Hi,"

        # Sign-off name
        signoff = user_name or "Vasundhara"

        # Check for target deadlines
        deadline_str = ""
        for t in tasks:
            if t.get("due_at"):
                due_dt = t["due_at"]
                deadline_str = due_dt.strftime("%I:%M %p on %d-%m-%Y").replace(" 0", " ")
                break

        full_content = f"{subject} {body}".lower()

        # Intent 1: Job application / Opportunity / Hiring
        if any(w in full_content for w in ["apply", "applying", "job", "position", "role", "interview", "candidate", "opportunity", "hiring", "interested in"]):
            deadline_mention = (
                f"I have noted the deadline of {deadline_str} and am submitting my confirmation accordingly."
                if deadline_str
                else "I am submitting my confirmation and interest for this role."
            )
            draft_body = (
                f"{salutation}\n\n"
                f"Thank you for reaching out regarding this opportunity.\n\n"
                f"I am definitely interested in applying for this position. {deadline_mention}\n\n"
                f"Please let me know the next steps in the application process or if you would like me to share an updated resume or any further details.\n\n"
                f"Best regards,\n{signoff}"
            )
            return draft_body

        # Intent 2: Meeting / Scheduling / Call
        if any(w in full_content for w in ["meeting", "schedule", "call", "zoom", "calendar", "sync", "catch up", "discuss"]):
            draft_body = (
                f"{salutation}\n\n"
                f"Thank you for your message regarding '{subject}'.\n\n"
                f"I would be glad to connect. I can confirm my availability for the suggested discussion. Please feel free to send over a calendar invite, or let me know if an alternate time works better for you.\n\n"
                f"Looking forward to our conversation.\n\n"
                f"Best regards,\n{signoff}"
            )
            return draft_body

        # Intent 3: Request for Information / Details / Documents
        if any(w in full_content for w in ["share the details", "could you share", "please provide", "send over", "send your", "documentation", "details below"]):
            draft_body = (
                f"{salutation}\n\n"
                f"Thank you for following up regarding '{subject}'.\n\n"
                f"I am reviewing the requested details and will share the required information with you shortly.\n\n"
                f"Please let me know if there are any specific guidelines or formats you would like me to include.\n\n"
                f"Best regards,\n{signoff}"
            )
            return draft_body

        # Intent 4: Explicit action items / tasks detected
        if tasks:
            task_bullets = "\n".join(f"- {t['title']}" for t in tasks[:3])
            deadline_line = f" Target deadline: {deadline_str}." if deadline_str else ""
            draft_body = (
                f"{salutation}\n\n"
                f"Thank you for your email regarding '{subject}'.\n\n"
                f"I have noted the following action item(s):{deadline_line}\n{task_bullets}\n\n"
                f"I am taking care of this right away and will keep you updated.\n\n"
                f"Best regards,\n{signoff}"
            )
            return draft_body

        # Intent 5: General professional response
        clean_subj = subject.strip()
        draft_body = (
            f"{salutation}\n\n"
            f"Thank you for reaching out regarding '{clean_subj}'.\n\n"
            f"I have received your message and am looking into it. I will get back to you with a comprehensive response shortly.\n\n"
            f"Best regards,\n{signoff}"
        )
        return draft_body

    async def analyze_email_and_save(
        self,
        email: Email,
        body_text: str,
        db: AsyncSession,
    ) -> EmailAnalysis:
        """
        Runs the full analysis pipeline for an email, stores:
        1. EmailAnalysis record
        2. Extracted Tasks
        3. Initial ReplyDraft (marked as 'pending' for user approval)
        """
        subject = email.subject or ""
        # 1. Hugging Face Models for Classification and Summarization (with robust fallback)
        hf_classification = await self.call_hf_classifier(f"{subject}. {body_text}")
        classification = hf_classification or self.classify_email(subject, body_text)

        hf_summary = await self.call_hf_summarizer(body_text)
        summary = hf_summary or self.summarize_email(subject, body_text)

        tasks_data = self.extract_tasks_and_deadlines(
            subject, body_text, category=classification.get("category")
        )

        # 1. Upsert EmailAnalysis
        stmt = select(EmailAnalysis).where(EmailAnalysis.email_id == email.id)
        existing_analysis = (await db.execute(stmt)).scalar_one_or_none()

        if not existing_analysis:
            analysis = EmailAnalysis(
                email_id=email.id,
                category=classification["category"],
                importance=classification["importance"],
                summary=summary,
                sentiment=classification["sentiment"],
                model_id=classification["model_id"],
                raw_entities={"extracted_task_count": len(tasks_data)},
            )
            db.add(analysis)
        else:
            analysis = existing_analysis
            analysis.category = classification["category"]
            analysis.importance = classification["importance"]
            analysis.summary = summary
            analysis.sentiment = classification["sentiment"]

        # 2. Save Extracted Tasks
        for t_info in tasks_data:
            t_stmt = select(Task).where(
                Task.email_id == email.id,
                Task.title == t_info["title"],
            )
            existing_task = (await db.execute(t_stmt)).scalar_one_or_none()
            if not existing_task:
                task = Task(
                    user_id=email.user_id,
                    email_id=email.id,
                    title=t_info["title"],
                    due_at=t_info.get("due_at"),
                    status="pending",
                )
                db.add(task)

        # 3. Generate suggested ReplyDraft ONLY for RECEIVED emails (never for sent emails)
        from app.db.models import User
        u_stmt = select(User).where(User.id == email.user_id)
        user_record = (await db.execute(u_stmt)).scalar_one_or_none()
        is_sent_by_user = False
        if user_record and email.sender:
            if user_record.email.lower() in email.sender.lower():
                is_sent_by_user = True

        if not is_sent_by_user and email.sender:
            d_stmt = select(ReplyDraft).where(
                ReplyDraft.email_id == email.id,
                ReplyDraft.status == "pending",
            )
            existing_draft = (await db.execute(d_stmt)).scalar_one_or_none()
            reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
            draft_content = self.generate_reply_draft(subject, email.sender, body_text, tasks_data)

            if existing_draft:
                existing_draft.body = draft_content
                existing_draft.subject = reply_subject
            else:
                reply_draft = ReplyDraft(
                    user_id=email.user_id,
                    email_id=email.id,
                    thread_id=email.thread_id,
                    recipient=email.sender,
                    subject=reply_subject,
                    body=draft_content,
                    status="pending",  # Strict safeguard: pending human review
                )
                db.add(reply_draft)

        await db.commit()
        return analysis


ai_service = AIService()

