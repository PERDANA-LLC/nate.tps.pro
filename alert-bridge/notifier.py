#!/usr/bin/env python3
"""
Shared notification module for trade alert system.
Sends to ntfy push + 4 email addresses (SMTP).
"""
import os
import smtplib
import requests
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.trade")

EMAILS = [
    os.environ.get("ALERT_EMAIL_1", ""),
    os.environ.get("ALERT_EMAIL_2", ""),
    os.environ.get("ALERT_EMAIL_3", ""),
    os.environ.get("ALERT_EMAIL_4", ""),
]


def _send_ntfy(body: str):
    channel = os.environ.get("NTFY_CHANNEL", "")
    if not channel:
        return
    try:
        requests.post(f"https://ntfy.sh/{channel}", data=body.encode(), timeout=5)
    except Exception as e:
        print(f"[notifier] ntfy error: {e}")


def _send_email(subject: str, body: str):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    if not (user and password):
        print("[notifier] SMTP not configured — skipping email")
        return
    for to_addr in EMAILS:
        if not to_addr:
            continue
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = user
            msg["To"] = to_addr
            with smtplib.SMTP(host, port) as server:
                server.starttls()
                server.login(user, password)
                server.sendmail(user, to_addr, msg.as_string())
            print(f"[notifier] email sent -> {to_addr}")
        except Exception as e:
            print(f"[notifier] email error -> {to_addr}: {e}")


def notify_alert(raw_alert: str, card_action: str):
    subject = f"ALERT RECEIVED: {card_action}"
    body = f"{card_action} alert detected\n{raw_alert}"
    _send_ntfy(body)
    _send_email(subject, body)


def notify_execution(msg: str, subject: str = "TRADE EXECUTED"):
    _send_ntfy(msg)
    _send_email(subject, msg)


def notify_error(msg: str):
    _send_ntfy(msg)
    _send_email("TRADE ERROR", msg)
