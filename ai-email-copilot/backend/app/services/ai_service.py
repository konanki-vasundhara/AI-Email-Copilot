"""Hugging Face AI Service for Email Analysis, Summarization, and Task Extraction."""
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
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

    def summarize_email(self, subject: str, body: str) -> str:
        """Generates a concise executive summary of the email."""
        clean_body = self.sanitize_untrusted_input(body)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean_body) if s.strip()]

        if not sentences:
            return f"Regarding '{subject}'."

        # Extract top 2 key sentences or format subject + opening
        if len(sentences) <= 2:
            return " ".join(sentences)
        return f"{sentences[0]} {sentences[1]}"

    def extract_tasks_and_deadlines(self, subject: str, body: str) -> List[Dict[str, Any]]:
        """
        Extracts action items, requests, and target deadlines from email content.
        """
        clean_text = self.sanitize_untrusted_input(f"{subject}. {body}")
        sentences = re.split(r"(?<=[.!?\n])\s+", clean_text)

        action_patterns = [
            r"(?i)\b(please|kindly|could you|can you|need to|must|ensure to|review|prepare|send|submit|approve|schedule)\b.*",
            r"(?i)\b(by\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|eod|noon|\d{1,2}(?:st|nd|rd|th)?\s+\w+))\b.*",
            r"(?i)\b(deadline\s+is\s+.*|due\s+by\s+.*)",
        ]

        extracted_tasks: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for s in sentences:
            s_clean = s.strip()
            if len(s_clean) < 10 or len(s_clean) > 250:
                continue

            for pattern in action_patterns:
                match = re.search(pattern, s_clean)
                if match:
                    # Estimate due date if mention found
                    due_at = None
                    lower_s = s_clean.lower()
                    if "tomorrow" in lower_s:
                        due_at = now + timedelta(days=1)
                    elif "friday" in lower_s:
                        days_until_friday = (4 - now.weekday()) % 7
                        if days_until_friday == 0:
                            days_until_friday = 7
                        due_at = now + timedelta(days=days_until_friday)
                    elif "eod" in lower_s or "today" in lower_s:
                        due_at = now.replace(hour=18, minute=0, second=0)

                    # Normalize task title
                    title = s_clean.rstrip(".")
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
    ) -> str:
        """
        Generates a context-aware professional draft reply.
        Crucial: This is merely a suggested draft and cannot be sent without user confirmation.
        """
        sender_name = sender.split("<")[0].replace('"', '').strip()
        salutation = f"Hi {sender_name}," if sender_name else "Hi,"

        if tasks:
            task_bullets = "\n".join(f"- {t['title']}" for t in tasks[:3])
            draft_body = (
                f"{salutation}\n\n"
                f"Thank you for your email regarding '{subject}'.\n\n"
                f"I have noted the following action items:\n{task_bullets}\n\n"
                f"I am on it and will follow up shortly.\n\n"
                f"Best regards,\nAI Email Copilot"
            )
        else:
            draft_body = (
                f"{salutation}\n\n"
                f"Thank you for reaching out regarding '{subject}'.\n\n"
                f"I have received your message and will review it in detail shortly.\n\n"
                f"Best regards,\nAI Email Copilot"
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
        classification = self.classify_email(subject, body_text)
        summary = self.summarize_email(subject, body_text)
        tasks_data = self.extract_tasks_and_deadlines(subject, body_text)

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
            # Check if task with identical title already exists for email
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

        # 3. Generate suggested ReplyDraft with status 'pending'
        d_stmt = select(ReplyDraft).where(
            ReplyDraft.email_id == email.id,
            ReplyDraft.status == "pending",
        )
        existing_draft = (await db.execute(d_stmt)).scalar_one_or_none()
        if not existing_draft and email.sender:
            reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
            draft_content = self.generate_reply_draft(subject, email.sender, body_text, tasks_data)

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

