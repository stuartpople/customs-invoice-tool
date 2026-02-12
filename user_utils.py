import os
import getpass

# Auto-capture username from system
def get_system_username():
    """Get the current system username automatically"""
    try:
        return getpass.getuser()
    except:
        return os.getenv('USERNAME', os.getenv('USER', 'Unknown'))

# Store conversion history
def log_conversion(username, direction, job_ref, num_files, timestamp):
    """Log each conversion to a history file"""
    log_entry = f"{timestamp}|{username}|{direction}|{job_ref}|{num_files} files\n"
    
    try:
        with open('conversion_history.log', 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except Exception as e:
        print(f"Could not log conversion: {e}")
