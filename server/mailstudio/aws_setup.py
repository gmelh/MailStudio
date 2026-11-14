"""Setting up bounce and complaint notifications in the owner's AWS account, so they
don't have to click through the SNS and SES consoles.

What gets created (each step is safe to repeat):
- an SNS topic, `mailstudio-notifications`, that only this account's SES may publish to;
- an SES configuration set, `mailstudio`, that sends bounce, complaint and delivery
  events to the topic (every campaign email is sent with it);
- an HTTPS subscription from the topic to this install's /webhooks/ses. SNS then posts
  a confirmation message there, which the webhook accepts automatically.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .repositories.settings import SesSettings

if TYPE_CHECKING:
    from mypy_boto3_sesv2 import SESV2Client
    from mypy_boto3_sns import SNSClient
    from mypy_boto3_sts import STSClient
else:
    SESV2Client = SNSClient = STSClient = Any

TOPIC_NAME = "mailstudio-notifications"
CONFIGURATION_SET = "mailstudio"
EVENT_DESTINATION = "mailstudio-notifications"
EVENT_TYPES: list[Any] = ["BOUNCE", "COMPLAINT", "DELIVERY"]

SubscriptionStatus = Literal["confirmed", "pending", "none"]

# The permissions the IAM user needs, shown to owners in the setup wizard.
IAM_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "MailStudio",
            "Effect": "Allow",
            "Action": [
                "ses:SendEmail",
                "ses:SendRawEmail",
                "ses:GetAccount",
                "ses:CreateEmailIdentity",
                "ses:GetEmailIdentity",
                "ses:CreateConfigurationSet",
                "ses:CreateConfigurationSetEventDestination",
                "ses:UpdateConfigurationSetEventDestination",
                "sns:CreateTopic",
                "sns:SetTopicAttributes",
                "sns:Subscribe",
                "sns:ListSubscriptionsByTopic",
            ],
            "Resource": "*",
        }
    ],
}


class Clients:
    def __init__(self, settings: SesSettings) -> None:
        options: dict[str, Any] = {
            "region_name": settings.region,
            "aws_access_key_id": settings.access_key_id,
            "aws_secret_access_key": settings.secret_access_key,
            "config": Config(
                retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=10, read_timeout=30
            ),
        }
        self.sns: SNSClient = boto3.client("sns", **options)
        self.sts: STSClient = boto3.client("sts", **options)
        self.ses: SESV2Client = boto3.client("sesv2", **options)


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def prepare(clients: Clients) -> str:
    """Create (or reuse) the topic and configuration set. Returns the topic ARN."""
    account = clients.sts.get_caller_identity()["Account"]
    topic = clients.sns.create_topic(Name=TOPIC_NAME)["TopicArn"]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowSesToPublish",
                "Effect": "Allow",
                "Principal": {"Service": "ses.amazonaws.com"},
                "Action": "sns:Publish",
                "Resource": topic,
                "Condition": {"StringEquals": {"AWS:SourceAccount": account}},
            }
        ],
    }
    clients.sns.set_topic_attributes(
        TopicArn=topic, AttributeName="Policy", AttributeValue=json.dumps(policy)
    )

    try:
        clients.ses.create_configuration_set(ConfigurationSetName=CONFIGURATION_SET)
    except ClientError as exc:
        if _code(exc) != "AlreadyExistsException":
            raise
    destination: Any = {
        "Enabled": True,
        "MatchingEventTypes": EVENT_TYPES,
        "SnsDestination": {"TopicArn": topic},
    }
    try:
        clients.ses.create_configuration_set_event_destination(
            ConfigurationSetName=CONFIGURATION_SET,
            EventDestinationName=EVENT_DESTINATION,
            EventDestination=destination,
        )
    except ClientError as exc:
        if _code(exc) != "AlreadyExistsException":
            raise
        clients.ses.update_configuration_set_event_destination(
            ConfigurationSetName=CONFIGURATION_SET,
            EventDestinationName=EVENT_DESTINATION,
            EventDestination=destination,
        )
    return topic


def subscribe(clients: Clients, topic: str, endpoint: str) -> None:
    """Ask SNS to deliver to this install. Repeating it with the same endpoint is harmless."""
    clients.sns.subscribe(TopicArn=topic, Protocol="https", Endpoint=endpoint)


def subscription_status(clients: Clients, topic: str, endpoint: str) -> SubscriptionStatus:
    pages = clients.sns.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=topic)
    for page in pages:
        for sub in page.get("Subscriptions", []):
            if sub.get("Endpoint") == endpoint:
                return "pending" if sub.get("SubscriptionArn") == "PendingConfirmation" else "confirmed"
    return "none"
