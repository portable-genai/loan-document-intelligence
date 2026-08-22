locals {
  security_signals = {
    guardrail_blocks = {
      description = "Loan-document guardrail blocked a request"
      filter      = "logName=\"projects/${var.project_id}/logs/loan-document-intelligence-audit\" AND jsonPayload.decision=\"blocked\""
    }
    service_account_key_creation = {
      description = "A service-account key was created or uploaded"
      filter      = "protoPayload.methodName=(\"google.iam.admin.v1.CreateServiceAccountKey\" OR \"google.iam.admin.v1.UploadServiceAccountKey\")"
    }
    vpc_sc_denials = {
      description = "VPC Service Controls denied a request"
      filter      = "protoPayload.metadata.@type=\"type.googleapis.com/google.cloud.audit.VpcServiceControlAuditMetadata\""
    }
    cmek_changes = {
      description = "A CMEK key or IAM policy changed"
      filter      = "protoPayload.serviceName=\"cloudkms.googleapis.com\" AND protoPayload.methodName=(\"CreateCryptoKeyVersion\" OR \"DestroyCryptoKeyVersion\" OR \"SetIamPolicy\" OR \"UpdateCryptoKey\")"
    }
  }
}

resource "google_monitoring_alert_policy" "security" {
  for_each = local.security_signals

  project               = var.project_id
  display_name          = "loan-document-intelligence: ${each.key}"
  combiner              = "OR"
  notification_channels = var.alert_notification_channels

  conditions {
    display_name = each.value.description
    condition_matched_log {
      filter = each.value.filter
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  depends_on = [google_project_service.required]
}
