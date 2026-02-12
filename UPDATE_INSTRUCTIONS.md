# Instructions to Auto-Capture Username and Add Conversion Logging

## Changes needed in app.py:

### 1. At the top of the file (around line 10), add:
```python
from user_utils import get_system_username, log_conversion
from datetime import datetime
```

### 2. Replace line 82-83:
FROM:
```python
if 'username' not in st.session_state:
    st.session_state.username = ""
```

TO:
```python
if 'username' not in st.session_state:
    st.session_state.username = get_system_username()  # Auto-capture from system
```

### 3. Replace lines 129-135 (the username text_input):
FROM:
```python
    username = st.text_input(
        "Username *",
        value=st.session_state.username,
        placeholder="Your name (required)",
        help="Required for audit trail"
    )
    st.session_state.username = username
```

TO:
```python
    # Username captured automatically from system
    username = st.session_state.username
    st.info(f"👤 Logged in as: **{username}**")
```

### 4. Around line 207 (in the processing section), add logging:
AFTER the line that starts processing, ADD:
```python
# Log this conversion
log_conversion(
    username=username,
    direction=direction,
    job_ref=job_reference or "No reference",
    num_files=len(uploaded_files),
    timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
)
```

These changes will:
- ✅ Auto-capture username from Windows/system login (no manual entry)
- ✅ Display who is logged in
- ✅ Log every conversion to conversion_history.log file
