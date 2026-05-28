"""
SMTP backend for email sending and sent mail archiving.
"""
import smtplib
import ssl
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate, make_msgid

from mail_utils import text_file_preview


def _create_ssl_context(skip_verify=False):
    ctx = ssl.create_default_context()
    if skip_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    except AttributeError:
        pass
    ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return ctx


def test_smtp_connection(smtp_server, smtp_port, use_ssl, user, password, skip_ssl_verify):
    ctx = _create_ssl_context(skip_verify=skip_ssl_verify)
    if use_ssl:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, context=ctx)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
    server.login(user, password)
    server.quit()
    return True


def send_email(smtp_server, smtp_port, use_ssl, user, password, to_addr,
               subject, body, attachment_paths, skip_ssl_verify, log_func,
               config=None):
    recipients = [r.strip() for r in to_addr.split(",") if r.strip()]
    if not recipients:
        raise ValueError("收件人列表为空")

    if attachment_paths:
        missing = [p for p in attachment_paths if not os.path.isfile(p)]
        if missing:
            log_func(f"  ⚠ 以下附件不存在: {missing}")
            attachment_paths = [p for p in attachment_paths if os.path.isfile(p)]
            if not attachment_paths:
                log_func("  所有附件均不存在，将以无附件方式发送")

    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject or "(无主题)"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    msg.attach(MIMEText(body or " ", "plain", "utf-8"))

    total_size = 0
    if attachment_paths:
        for att_path in attachment_paths:
            if os.path.isfile(att_path):
                filename = os.path.basename(att_path)
                file_size = os.path.getsize(att_path)
                with open(att_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment",
                                filename=("utf-8", "", filename))
                msg.attach(part)
                total_size += file_size
                log_func(f"  附件: {filename} ({file_size} 字节)")
                preview = text_file_preview(att_path)
                if preview:
                    log_func(f"  附件预览(文本):\n{preview[:300]}")
        log_func(f"  共 {len(attachment_paths)} 个附件，总大小 {total_size} 字节")
    else:
        log_func("  无附件")

    ctx = _create_ssl_context(skip_verify=skip_ssl_verify)
    if use_ssl:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, context=ctx)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()

    server.login(user, password)
    server.sendmail(user, recipients, msg.as_string())
    server.quit()
    log_func(f"  邮件已发送 -> {', '.join(recipients)}")

    if config:
        from imap_backend import archive_to_sent
        archive_to_sent(config, msg.as_bytes(), log_func)