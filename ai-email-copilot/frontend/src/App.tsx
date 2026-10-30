import { useState, useEffect } from 'react';
import {
  Mail,
  CheckSquare,
  RefreshCw,
  Search,
  Send,
  AlertTriangle,
  Clock,
  Trash2,
  ShieldCheck,
  LogOut,
  Sparkles,
  Inbox,
  X,
} from 'lucide-react';
import type { EmailItem, TaskItem, ReplyDraftItem, AuthStatusResponse } from './types';
import * as api from './services/api';

export default function App() {
  const [currentUserEmail, setCurrentUserEmail] = useState<string>(() => {
    return localStorage.getItem('ai_copilot_email') || 'test.user@example.com';
  });

  const [authStatus, setAuthStatus] = useState<AuthStatusResponse | null>(null);
  const [activeTab, setActiveTab] = useState<'inbox' | 'tasks'>('inbox');
  const [emails, setEmails] = useState<EmailItem[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [syncing, setSyncing] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCategory, setSelectedCategory] = useState<string>('All');

  // Selected Email for Modal & Reply Flow
  const [selectedEmail, setSelectedEmail] = useState<EmailItem | null>(null);
  const [replyDraft, setReplyDraft] = useState<ReplyDraftItem | null>(null);
  const [draftBody, setDraftBody] = useState<string>('');
  const [confirmSend, setConfirmSend] = useState<boolean>(false);
  const [sendingReply, setSendingReply] = useState<boolean>(false);
  const [sendSuccess, setSendSuccess] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Handle returning from OAuth callback (?connected=true&email=...)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const emailParam = params.get('email');
    const connectedParam = params.get('connected');
    if (emailParam) {
      setCurrentUserEmail(emailParam);
      localStorage.setItem('ai_copilot_email', emailParam);
    }
    if (connectedParam === 'true') {
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }, []);

  // Check auth and load data on user change
  useEffect(() => {
    if (currentUserEmail) {
      localStorage.setItem('ai_copilot_email', currentUserEmail);
      loadAuthStatus();
      loadEmails();
      loadTasks();
    }
  }, [currentUserEmail, selectedCategory]);

  const loadAuthStatus = async () => {
    try {
      const status = await api.getAuthStatus(currentUserEmail);
      setAuthStatus(status);
    } catch {
      setAuthStatus({ connected: false });
    }
  };

  const loadEmails = async () => {
    setLoading(true);
    try {
      const data = await api.listEmails(currentUserEmail, selectedCategory, searchQuery);
      setEmails(data);
    } catch (err: any) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const loadTasks = async () => {
    try {
      const data = await api.listTasks(currentUserEmail);
      setTasks(data);
    } catch (err) {
      console.error(err);
    }
  };

  const handleConnectGmail = async () => {
    try {
      const url = await api.getGoogleLoginUrl();
      window.location.href = url;
    } catch (err: any) {
      alert(`Failed to initiate Google login: ${err.message}`);
    }
  };

  const handleDisconnect = async () => {
    if (!confirm('Are you sure you want to disconnect Gmail and revoke application tokens?')) return;
    try {
      await api.disconnectGoogle(currentUserEmail);
      await loadAuthStatus();
      alert('Gmail connection revoked and credentials purged.');
    } catch (err: any) {
      alert(`Error disconnecting: ${err.message}`);
    }
  };

  const handleDeleteAllData = async () => {
    if (
      !confirm(
        'CRITICAL PRIVACY ACTION: Are you sure you want to permanently delete all emails, AI summaries, tasks, reply drafts, and disconnect Gmail? This action cannot be undone.'
      )
    )
      return;
    try {
      await api.deleteUserData(currentUserEmail);
      setEmails([]);
      setTasks([]);
      setAuthStatus({ connected: false });
      alert('All application data has been permanently deleted.');
    } catch (err: any) {
      alert(`Error deleting data: ${err.message}`);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    setErrorMessage(null);
    try {
      const res = await api.syncEmails(currentUserEmail);
      alert(res.message);
      await loadEmails();
      await loadTasks();
    } catch (err: any) {
      setErrorMessage(err.message);
    } finally {
      setSyncing(false);
    }
  };

  const handleOpenEmail = async (email: EmailItem) => {
    setSelectedEmail(email);
    setConfirmSend(false);
    setSendSuccess(false);
    setErrorMessage(null);

    // Fetch existing reply draft if any
    try {
      const drafts = await api.listReplies(currentUserEmail, email.id);
      if (drafts.length > 0) {
        setReplyDraft(drafts[0]);
        setDraftBody(drafts[0].body);
      } else {
        setReplyDraft(null);
        setDraftBody('');
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleGenerateDraft = async () => {
    if (!selectedEmail) return;
    try {
      const draft = await api.generateReplyDraft(selectedEmail.id, currentUserEmail);
      setReplyDraft(draft);
      setDraftBody(draft.body);
    } catch (err: any) {
      alert(`Draft generation failed: ${err.message}`);
    }
  };

  const handleApproveAndSend = async () => {
    if (!replyDraft || !confirmSend) return;
    setSendingReply(true);
    setErrorMessage(null);

    try {
      const updated = await api.approveAndSendReply(replyDraft.id, currentUserEmail, draftBody);
      setReplyDraft(updated);
      setSendSuccess(true);
    } catch (err: any) {
      setErrorMessage(err.message);
    } finally {
      setSendingReply(false);
    }
  };

  const handleToggleTask = async (task: TaskItem) => {
    const nextStatus = task.status === 'completed' ? 'pending' : 'completed';
    try {
      await api.updateTask(task.id, currentUserEmail, { status: nextStatus });
      await loadTasks();
    } catch (err: any) {
      alert(`Failed to update task: ${err.message}`);
    }
  };

  const handleDeleteTask = async (taskId: string) => {
    try {
      await api.deleteTask(taskId, currentUserEmail);
      await loadTasks();
    } catch (err: any) {
      alert(`Failed to delete task: ${err.message}`);
    }
  };

  const categories = [
    'All',
    'Work',
    'Action Required',
    'Personal',
    'Financial / Billing',
    'Newsletter / Marketing',
  ];

  return (
    <div>
      {/* Top Navigation Bar */}
      <header className="app-header">
        <div className="brand">
          <div className="brand-icon">
            <Sparkles size={20} />
          </div>
          <span>AI Email Copilot</span>
        </div>

        <div className="header-actions">
          {/* Active Email User Switcher */}
          <input
            type="email"
            value={currentUserEmail}
            onChange={(e) => setCurrentUserEmail(e.target.value)}
            title="Active user email identifier"
            className="search-input"
            style={{ width: '220px', padding: '0.4rem 0.6rem' }}
          />

          {/* Connection Status Badge */}
          {authStatus?.connected ? (
            <div className="status-pill status-connected">
              <span className="pulse-dot green" />
              <span>Gmail Connected</span>
              <button
                onClick={handleDisconnect}
                className="btn btn-outline"
                style={{ padding: '0.2rem 0.4rem', marginLeft: '0.5rem', fontSize: '0.75rem' }}
                title="Disconnect Gmail"
              >
                <LogOut size={12} /> Disconnect
              </button>
              <button
                onClick={handleDeleteAllData}
                className="btn btn-danger"
                style={{ padding: '0.2rem 0.4rem', marginLeft: '0.35rem', fontSize: '0.75rem' }}
                title="Permanently purge all stored emails, tasks, and disconnect account"
              >
                <Trash2 size={12} /> Purge Data
              </button>
            </div>
          ) : (
            <div className="status-pill status-disconnected">
              <span className="pulse-dot red" />
              <span>Not Connected</span>
              <button
                onClick={handleConnectGmail}
                className="btn btn-primary"
                style={{ padding: '0.25rem 0.6rem', marginLeft: '0.5rem', fontSize: '0.75rem' }}
              >
                Connect Gmail
              </button>
            </div>
          )}

          {/* Sync Trigger */}
          <button
            onClick={handleSync}
            disabled={syncing}
            className="btn btn-outline"
            title="Synchronize recent emails from Gmail"
          >
            <RefreshCw size={15} className={syncing ? 'spin' : ''} />
            <span>{syncing ? 'Syncing...' : 'Sync Inbox'}</span>
          </button>
        </div>
      </header>

      {/* Tabs */}
      <nav className="tabs-bar">
        <button
          className={`tab-btn ${activeTab === 'inbox' ? 'active' : ''}`}
          onClick={() => setActiveTab('inbox')}
        >
          <Inbox size={18} />
          <span>Inbox & Analyses ({emails.length})</span>
        </button>
        <button
          className={`tab-btn ${activeTab === 'tasks' ? 'active' : ''}`}
          onClick={() => setActiveTab('tasks')}
        >
          <CheckSquare size={18} />
          <span>Tasks & Deadlines ({tasks.filter((t) => t.status === 'pending').length})</span>
        </button>
      </nav>

      {/* Main Content Area */}
      <main className="main-container">
        {errorMessage && (
          <div className="safeguard-banner" style={{ borderColor: '#fca5a5', background: '#fef2f2', color: '#991b1b' }}>
            <AlertTriangle size={18} />
            <span>{errorMessage}</span>
          </div>
        )}

        {activeTab === 'inbox' && (
          <>
            {/* Search and Category Filters */}
            <div className="filter-row">
              <div className="search-input">
                <Search size={16} color="#94a3b8" />
                <input
                  type="text"
                  placeholder="Search subject or sender..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && loadEmails()}
                />
              </div>

              <div className="category-chips">
                {categories.map((cat) => (
                  <button
                    key={cat}
                    className={`category-chip ${selectedCategory === cat ? 'active' : ''}`}
                    onClick={() => setSelectedCategory(cat)}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            </div>

            {/* Email List */}
            {loading ? (
              <div style={{ textAlign: 'center', padding: '3rem', color: '#94a3b8' }}>
                Loading emails...
              </div>
            ) : emails.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '4rem 1rem', background: '#ffffff', borderRadius: '0.75rem', border: '1px solid #e2e8f0' }}>
                <Mail size={40} color="#94a3b8" style={{ marginBottom: '1rem' }} />
                <h3>No emails found</h3>
                <p style={{ color: '#64748b', fontSize: '0.9rem', marginTop: '0.5rem' }}>
                  Connect your Gmail account and click <strong>Sync Inbox</strong> to import and analyze messages.
                </p>
              </div>
            ) : (
              emails.map((email) => {
                const importance = email.analysis?.importance || 'Normal';
                const badgeClass =
                  importance === 'High'
                    ? 'badge-high'
                    : importance === 'Low'
                    ? 'badge-low'
                    : 'badge-normal';

                return (
                  <div
                    key={email.id}
                    className="email-card"
                    onClick={() => handleOpenEmail(email)}
                  >
                    <div className="email-card-header">
                      <div className="email-badges">
                        <span className={`badge ${badgeClass}`}>{importance}</span>
                        {email.analysis?.category && (
                          <span className="badge badge-category">{email.analysis.category}</span>
                        )}
                        {email.analysis?.sentiment && (
                          <span style={{ fontSize: '0.75rem', color: '#64748b' }}>
                            • {email.analysis.sentiment}
                          </span>
                        )}
                      </div>
                      <span className="email-date">
                        {new Date(email.received_at).toLocaleDateString()} {new Date(email.received_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>

                    <div className="email-subject">{email.subject || '(No Subject)'}</div>
                    <div className="email-sender">From: {email.sender}</div>

                    {email.analysis?.summary ? (
                      <div className="email-summary-box">
                        <strong>AI Summary:</strong> {email.analysis.summary}
                      </div>
                    ) : (
                      <p style={{ fontSize: '0.875rem', color: '#64748b' }}>
                        {email.snippet || 'No preview available.'}
                      </p>
                    )}
                  </div>
                );
              })
            )}
          </>
        )}

        {/* Tasks View */}
        {activeTab === 'tasks' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {tasks.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '3rem', background: '#fff', borderRadius: '0.5rem', border: '1px solid #e2e8f0' }}>
                <CheckSquare size={36} color="#94a3b8" style={{ marginBottom: '0.5rem' }} />
                <h3>No tasks extracted yet</h3>
                <p style={{ color: '#64748b', fontSize: '0.875rem' }}>
                  Action items and deadlines detected by Hugging Face models will populate here automatically.
                </p>
              </div>
            ) : (
              tasks.map((task) => (
                <div key={task.id} className={`task-item ${task.status === 'completed' ? 'completed' : ''}`}>
                  <div className="task-left">
                    <input
                      type="checkbox"
                      checked={task.status === 'completed'}
                      onChange={() => handleToggleTask(task)}
                      style={{ width: '1.2rem', height: '1.2rem', cursor: 'pointer' }}
                    />
                    <div>
                      <div style={{ fontWeight: 500, fontSize: '0.95rem' }}>{task.title}</div>
                      {task.due_at && (
                        <div style={{ marginTop: '0.25rem' }}>
                          <span className="due-pill">
                            <Clock size={11} /> Due: {new Date(task.due_at).toLocaleDateString()}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>

                  <button
                    onClick={() => handleDeleteTask(task.id)}
                    className="btn btn-outline"
                    style={{ padding: '0.35rem 0.5rem', color: '#94a3b8' }}
                    title="Delete task"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))
            )}
          </div>
        )}
      </main>

      {/* Email Detail & Reply Approval Safeguard Modal */}
      {selectedEmail && (
        <div className="modal-overlay" onClick={() => setSelectedEmail(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ fontSize: '1.1rem', fontWeight: 600 }}>Email Details & AI Draft</h3>
              <button
                onClick={() => setSelectedEmail(null)}
                style={{ background: 'none', border: 'none', cursor: 'pointer' }}
              >
                <X size={20} />
              </button>
            </div>

            <div className="modal-body">
              {/* Header Details */}
              <div>
                <h4 style={{ fontSize: '1.15rem', marginBottom: '0.3rem' }}>{selectedEmail.subject}</h4>
                <div style={{ fontSize: '0.85rem', color: '#64748b' }}>
                  <strong>From:</strong> {selectedEmail.sender}
                </div>
                <div style={{ fontSize: '0.85rem', color: '#64748b' }}>
                  <strong>Date:</strong> {new Date(selectedEmail.received_at).toLocaleString()}
                </div>
              </div>

              {/* AI Summary Box */}
              {selectedEmail.analysis && (
                <div className="email-summary-box">
                  <div style={{ fontWeight: 600, marginBottom: '0.2rem', color: '#1e40af' }}>
                    AI Summary ({selectedEmail.analysis.category} • {selectedEmail.analysis.importance} Priority)
                  </div>
                  <div>{selectedEmail.analysis.summary}</div>
                </div>
              )}

              {/* Strict Human-in-the-Loop Reply Section */}
              <div style={{ borderTop: '1px solid #e2e8f0', paddingTop: '1.25rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                  <h4 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <ShieldCheck size={18} color="#2563eb" />
                    <span>Reply Draft Approval Safeguard</span>
                  </h4>

                  {!replyDraft && (
                    <button onClick={handleGenerateDraft} className="btn btn-outline" style={{ fontSize: '0.8rem' }}>
                      <Sparkles size={14} /> Generate Draft
                    </button>
                  )}
                </div>

                {/* Safeguard Warning Banner */}
                <div className="safeguard-banner">
                  <AlertTriangle size={18} style={{ flexShrink: 0, marginTop: '2px' }} />
                  <div>
                    <strong>Zero Autonomous Sends:</strong> The assistant generates suggested text, but
                    never delivers an email without your explicit review and confirmation.
                  </div>
                </div>

                {replyDraft ? (
                  <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                    <div>
                      <label style={{ fontSize: '0.825rem', fontWeight: 600, color: '#475569', display: 'block', marginBottom: '0.3rem' }}>
                        Editable Draft Body:
                      </label>
                      <textarea
                        className="reply-editor"
                        value={draftBody}
                        onChange={(e) => setDraftBody(e.target.value)}
                        disabled={replyDraft.status === 'sent'}
                      />
                    </div>

                    {sendSuccess && (
                      <div className="status-pill status-connected" style={{ padding: '0.6rem 1rem', width: '100%' }}>
                        <ShieldCheck size={16} />
                        <span>Email successfully sent and confirmed via Gmail!</span>
                      </div>
                    )}

                    {replyDraft.status === 'sent' ? (
                      <div className="status-pill status-connected" style={{ padding: '0.6rem 1rem' }}>
                        <ShieldCheck size={16} />
                        <span>Email dispatched through Gmail on {new Date(replyDraft.sent_at || '').toLocaleString()}.</span>
                      </div>
                    ) : (
                      <>
                        <label className="confirm-checkbox">
                          <input
                            type="checkbox"
                            checked={confirmSend}
                            onChange={(e) => setConfirmSend(e.target.checked)}
                          />
                          <span>
                            I have reviewed this draft and explicitly confirm sending it through my Gmail account.
                          </span>
                        </label>

                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.75rem' }}>
                          <button
                            onClick={() => setSelectedEmail(null)}
                            className="btn btn-outline"
                          >
                            Cancel
                          </button>
                          <button
                            onClick={handleApproveAndSend}
                            disabled={!confirmSend || sendingReply}
                            className="btn btn-primary"
                          >
                            <Send size={15} />
                            <span>{sendingReply ? 'Dispatching...' : 'Approve & Send via Gmail'}</span>
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ) : (
                  <p style={{ fontSize: '0.875rem', color: '#64748b', marginTop: '0.5rem' }}>
                    Click <strong>Generate Draft</strong> above to have Hugging Face compose a contextual response.
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
