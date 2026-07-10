"""Conversational workspace: a ChatGPT-style chat layer over the existing agents.

Every researched company becomes a chat thread. The chat AGENT (chat/agent.py)
orchestrates CAPABILITIES exposed as callable TOOLS (chat/tools.py) via Claude
tool-use — research, email drafting/revision today; send-email, find-prospects,
handle-replies, LinkedIn outreach tomorrow. It never re-implements agent logic
and it reuses a thread's existing research instead of re-researching.

This package sits ON TOP of the research engine and email writer; it does not
modify them. Persistence lives in chat/store.py; the Streamlit UI is chat_app.py.
"""
