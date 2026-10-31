import type { EmailItem, TaskItem, ReplyDraftItem, AuthStatusResponse } from '../types';

const API_BASE = '/api/v1';

export async function getAuthStatus(email: string): Promise<AuthStatusResponse> {
  const res = await fetch(`${API_BASE}/auth/status?email=${encodeURIComponent(email)}`);
  if (!res.ok) throw new Error('Failed to fetch auth status');
  return res.json();
}

export async function getGoogleLoginUrl(): Promise<string> {
  const res = await fetch(`${API_BASE}/auth/google/login`);
  if (!res.ok) throw new Error('Failed to fetch login URL');
  const data = await res.json();
  return data.authorization_url;
}

export async function disconnectGoogle(email: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/google/disconnect?email=${encodeURIComponent(email)}`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error('Failed to disconnect Gmail');
}

export async function deleteUserData(email: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/user/delete-data?email=${encodeURIComponent(email)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete application data');
}

export async function listEmails(
  userEmail: string,
  category?: string,
  search?: string,
  folder?: string
): Promise<EmailItem[]> {
  const params = new URLSearchParams({ user_email: userEmail, limit: '100' });
  if (category && category !== 'All') params.append('category', category);
  if (search) params.append('search', search);
  if (folder) params.append('folder', folder);

  const res = await fetch(`${API_BASE}/emails/?${params.toString()}`);
  if (!res.ok) throw new Error('Failed to fetch emails');
  return res.json();
}

export async function syncEmails(userEmail: string): Promise<{ count: number; message: string }> {
  const res = await fetch(`${API_BASE}/emails/sync?user_email=${encodeURIComponent(userEmail)}&limit=50`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to sync emails');
  }
  return res.json();
}

export async function listTasks(userEmail: string, status?: string): Promise<TaskItem[]> {
  const params = new URLSearchParams({ user_email: userEmail });
  if (status && status !== 'all') params.append('status', status);

  const res = await fetch(`${API_BASE}/tasks/?${params.toString()}`);
  if (!res.ok) throw new Error('Failed to fetch tasks');
  return res.json();
}

export async function updateTask(
  taskId: string,
  userEmail: string,
  updates: Partial<TaskItem>
): Promise<TaskItem> {
  const res = await fetch(
    `${API_BASE}/tasks/${taskId}?user_email=${encodeURIComponent(userEmail)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    }
  );
  if (!res.ok) throw new Error('Failed to update task');
  return res.json();
}

export async function deleteTask(taskId: string, userEmail: string): Promise<void> {
  const res = await fetch(
    `${API_BASE}/tasks/${taskId}?user_email=${encodeURIComponent(userEmail)}`,
    { method: 'DELETE' }
  );
  if (!res.ok) throw new Error('Failed to delete task');
}

export async function clearCompletedTasks(userEmail: string): Promise<void> {
  const res = await fetch(
    `${API_BASE}/tasks/completed/clear?user_email=${encodeURIComponent(userEmail)}`,
    { method: 'DELETE' }
  );
  if (!res.ok) throw new Error('Failed to clear completed tasks');
}

export async function listReplies(userEmail: string, emailId?: string): Promise<ReplyDraftItem[]> {
  const params = new URLSearchParams({ user_email: userEmail });
  if (emailId) params.append('email_id', emailId);

  const res = await fetch(`${API_BASE}/replies/?${params.toString()}`);
  if (!res.ok) throw new Error('Failed to fetch reply drafts');
  return res.json();
}

export async function generateReplyDraft(
  emailId: string,
  userEmail: string
): Promise<ReplyDraftItem> {
  const res = await fetch(
    `${API_BASE}/replies/generate?email_id=${emailId}&user_email=${encodeURIComponent(userEmail)}`,
    { method: 'POST' }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to generate draft');
  }
  return res.json();
}

export async function updateReplyDraft(
  draftId: string,
  userEmail: string,
  body: string
): Promise<ReplyDraftItem> {
  const res = await fetch(
    `${API_BASE}/replies/${draftId}?user_email=${encodeURIComponent(userEmail)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body }),
    }
  );
  if (!res.ok) throw new Error('Failed to update reply draft');
  return res.json();
}

export async function approveAndSendReply(
  draftId: string,
  userEmail: string,
  editedBody?: string
): Promise<ReplyDraftItem> {
  const res = await fetch(
    `${API_BASE}/replies/${draftId}/approve?user_email=${encodeURIComponent(userEmail)}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true, edited_body: editedBody }),
    }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to send reply');
  }
  return res.json();
}
