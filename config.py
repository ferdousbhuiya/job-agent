import os
from dotenv import load_dotenv

load_dotenv()

def get_key(key_name):
    """Fetches a secret key from env (.env). Streamlit fallback kept for old dashboards."""
    try:
        return os.getenv(key_name)
    except Exception:
        try:
            import streamlit as st
            return st.secrets[key_name]
        except Exception:
            return None