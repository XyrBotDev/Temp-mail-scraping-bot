📧 Temp Mail Scraping Bot

A Telegram-based temporary email bot that lets users create temporary mail sessions, check incoming messages, view email details, and manage their active mailbox directly from Telegram.

✨ Features

- 🟢 Create New Mail
  
  - Generate a new temporary email address.
  - Uses multiple temporary-mail providers.
  - Automatic provider fallback when a provider is unavailable.

- 🔵 Check Inbox
  
  - Check incoming emails from your active mailbox.
  - View:
    - Sender
    - Subject
    - Message content
    - Available links

- 🟡 Current Mail
  
  - View your currently active temporary email.
  - Check the active provider and session status.

- 🔴 Delete Session
  
  - Delete the current temporary-mail session.
  - Stop automatic inbox refreshing for that session.

- 🔄 Automatic Inbox Refresh
  
  - Active inboxes are checked automatically.
  - New incoming emails can be detected without manually checking every time.

- 🌐 Multiple Providers
  
  - Uses a collection of temporary-mail services.
  - Automatically tries another provider when the current provider fails.

- 📊 User Statistics
  
  - Track bot usage and user activity.

- 🛡️ Admin Controls
  
  - Broadcast messages
  - Ban / unban users
  - Warning system
  - User list
  - Statistics
  - Top users
  - Export user data

- 🚀 Render Compatible
  
  - Includes a lightweight web server and health endpoint for deployment on platforms such as Render.

---

🛠️ Tech Stack

- Python
- Aiogram
- aiohttp
- AsyncIO
- JSON-based storage
- Telegram Bot API

---

📋 Requirements

- Python 3.10+
- Telegram Bot Token
- Internet connection
- Temporary-mail provider endpoints/APIs used by the bot

---

⚙️ Installation

1. Clone the repository

git clone https://github.com/XyrBotDev/Temp-mail-scraping-bot.git
cd Temp-mail-scraping-bot

2. Install dependencies

pip install -r requirements.txt

3. Configure the bot

Open "bot.py" and configure your Telegram bot token and administrator settings as required by the project.

Never publish your Telegram bot token or other private credentials in a public repository.

4. Run the bot

python bot.py

---

🤖 Bot Commands

User Commands

/start

Starts the bot and opens the main menu.

Admin Commands

/broadcast
/ban
/unban
/banlist
/warn
/unwarn
/warmlist
/warnlist
/stats
/topusers
/userlist
/export

Admin commands are intended for authorized administrators only.

---

📱 Main Menu

The bot provides a simple Telegram keyboard:

🟢 Create New Mail
🔵 Check Inbox
🟡 Current Mail
🔴 Delete Session

---

🔄 How It Works

User
  │
  ▼
Telegram Bot
  │
  ├── Create temporary mail
  │
  ├── Select available provider
  │
  ├── Store active session
  │
  ├── Check inbox
  │
  └── Notify user about new messages

When a provider is unavailable, the bot can attempt another configured provider.

---

🗂️ Project Structure

Temp-mail-scraping-bot/
│
├── bot.py
├── requirements.txt
├── Procfile
├── README.md
└── database.json

«"database.json" may be created/used by the bot for local user and bot-management data depending on the deployment configuration.»

---

🚀 Deploy on Render

The project includes a "Procfile" and an HTTP health endpoint to make deployment on services such as Render easier.

Basic Render setup

1. Create a new Web Service.
2. Connect your GitHub repository.
3. Select the repository.
4. Set the appropriate Python environment.
5. Install dependencies from "requirements.txt".
6. Use the command specified by the project's "Procfile".
7. Add your required environment variables/secrets.
8. Deploy the service.

The application includes a health endpoint that can be used to verify that the web service is running.

---

🔐 Security

Do not commit sensitive information such as:

- Telegram bot tokens
- API keys
- Private credentials
- Authentication tokens
- Personal account information

Use environment variables or another secure secret-management method for production deployments.

---

⚠️ Important Notes

Temporary email services can change their APIs, domains, response formats, availability, or access policies without notice.

Because this project integrates with multiple external providers:

- Some providers may become unavailable.
- Some providers may require changes to their API.
- Inbox availability may vary by provider.
- A generated email address does not necessarily guarantee permanent mailbox availability.
- Provider-specific behavior may differ.

Use the project only with services and providers you are authorized to access.

---

📜 Disclaimer

This project is provided for educational and legitimate development purposes.

Users are responsible for complying with the terms of service, policies, and applicable laws of Telegram and the temporary-email providers used by the project.

The developers are not responsible for misuse of the software or third-party services.

---

⭐ Support the Project

If you find this project useful:

- ⭐ Star the repository
- 🐛 Report bugs through GitHub Issues
- 💡 Suggest improvements
- 🔧 Contribute improvements through pull requests

---

📄 License

Add your preferred open-source license to this repository.

For example:

MIT License

See the repository's "LICENSE" file for the applicable license terms.

---

🔗 Repository

GitHub:
https://github.com/XyrBotDev/Temp-mail-scraping-bot

Telegram Bot:
@SmailiproBot
