import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
import settings

logger = logging.getLogger(__name__)

_S3_PRESIGNED_EXPIRY = 7 * 24 * 3600  # 7 days
_SMTP_PORT = 587


def _s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )


def upload_to_s3(excel_bytes: bytes, filename: str) -> str:
    """Upload Excel bytes to S3 under exports/ and return a presigned download URL."""
    s3 = _s3_client()
    key = f"exports/{filename}"
    s3.put_object(
        Bucket=settings.AWS_S3_BUCKET_NAME,
        Key=key,
        Body=excel_bytes,
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.AWS_S3_BUCKET_NAME, "Key": key},
        ExpiresIn=_S3_PRESIGNED_EXPIRY,
    )
    logger.info("Uploaded %s to S3 bucket %s", filename, settings.AWS_S3_BUCKET_NAME)
    return url


def send_results_email(excel_bytes: bytes, filename: str, s3_url: str) -> None:
    """Send Excel file as an attachment via SES SMTP to all configured recipients.

    AWS_SES_USERNAME / AWS_SES_PASSWORD are the SMTP credentials generated from
    the SES console — they are NOT IAM API keys, so boto3 SES client cannot use them.
    """
    recipients = settings.RECIPIENT_EMAILS
    smtp_host = f"email-smtp.{settings.AWS_REGION}.amazonaws.com"

    msg = MIMEMultipart()
    msg["Subject"] = "GSA Scraping Completed — Results Attached"
    msg["From"] = settings.AWS_SES_FROM_EMAIL
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(
        "The GSA scraping job has completed.\n\n"
        "The results Excel file is attached to this email.\n\n"
        "You can also download it directly from S3 (link valid for 7 days):\n"
        f"{s3_url}",
        "plain",
    ))

    attachment = MIMEApplication(excel_bytes, Name=filename)
    attachment["Content-Disposition"] = f'attachment; filename="{filename}"'
    msg.attach(attachment)

    with smtplib.SMTP(smtp_host, _SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(settings.AWS_SES_USERNAME, settings.AWS_SES_PASSWORD)
        server.sendmail(settings.AWS_SES_FROM_EMAIL, recipients, msg.as_string())

    logger.info("Results email sent via SES SMTP to: %s", recipients)


def notify_scraping_complete() -> None:
    """Generate the Excel export, upload to S3, and email recipients. Safe to call from any thread."""
    if not settings.RECIPIENT_EMAILS:
        logger.warning("RECIPIENT_EMAILS not configured — skipping post-scrape notification")
        return
    if not settings.AWS_S3_BUCKET_NAME or not settings.AWS_ACCESS_KEY_ID:
        logger.warning("AWS S3 not configured — skipping post-scrape notification")
        return
    if not settings.AWS_SES_FROM_EMAIL or not settings.AWS_SES_USERNAME:
        logger.warning("AWS SES not configured — skipping post-scrape notification")
        return

    # Import here to avoid circular imports at module load time
    from services.export_service import export_to_excel

    result = export_to_excel()
    if not result:
        logger.info("No scraped data available to export — skipping notification")
        return

    buffer, filename = result
    excel_bytes = buffer.getvalue()

    try:
        s3_url = upload_to_s3(excel_bytes, filename)
    except Exception:
        logger.exception("S3 upload failed — email will not be sent")
        return

    try:
        send_results_email(excel_bytes, filename, s3_url)
    except Exception:
        logger.exception("SES email send failed")
