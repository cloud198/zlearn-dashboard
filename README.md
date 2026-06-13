# ZLearn Real-Time Dashboard

Real-time dashboard for monitoring batch enrollments and session attendance.

## Features

- 📊 Category overview with enrollment counts
- 📦 Batch-wise enrollment details
- 🎓 Session-wise attendance tracking
- 👥 User attendance details
- 🔄 Auto-refresh every 30 seconds

## Local Development

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.streamlit/secrets.toml` file:
```toml
MONGO_URI = "mongodb://username:password@host:port/?tls=false"
DB_NAME = "nism-platform"
```

3. Run the app:
```bash
streamlit run app.py
```

## Deployment to Streamlit Cloud

1. Push code to GitHub
2. Go to https://streamlit.io/cloud
3. Sign in with GitHub
4. Create new app, select your repo
5. Add secrets in Streamlit Cloud settings
6. Deploy!
