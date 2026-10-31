export interface User {
  id: string;
  email: string;
  display_name?: string | null;
}

export interface EmailAnalysis {
  id: string;
  email_id: string;
  category: string;
  importance: string;
  summary: string;
  sentiment?: string | null;
  model_id: string;
  created_at: string;
}

export interface EmailItem {
  id: string;
  user_id: string;
  gmail_message_id: string;
  thread_id: string;
  subject?: string | null;
  sender?: string | null;
  snippet?: string | null;
  received_at: string;
  created_at: string;
  analysis?: EmailAnalysis | null;
}

export interface TaskEmailSummary {
  id: string;
  subject?: string | null;
  sender?: string | null;
  received_at: string;
}

export interface TaskItem {
  id: string;
  user_id: string;
  email_id?: string | null;
  title: string;
  description?: string | null;
  due_at?: string | null;
  status: 'pending' | 'completed' | 'dismissed';
  created_at: string;
  updated_at: string;
  email?: TaskEmailSummary | null;
}

export interface ReplyDraftItem {
  id: string;
  user_id: string;
  email_id?: string | null;
  thread_id: string;
  recipient: string;
  subject: string;
  body: string;
  status: 'pending' | 'approved' | 'sending' | 'sent' | 'failed' | 'cancelled';
  approved_at?: string | null;
  sent_at?: string | null;
  send_error?: string | null;
  idempotency_key?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AuthStatusResponse {
  connected: boolean;
  user_id?: string;
  email?: string;
  display_name?: string | null;
  granted_scopes?: string[];
  has_refresh_token?: boolean;
  expires_at?: string | null;
}

