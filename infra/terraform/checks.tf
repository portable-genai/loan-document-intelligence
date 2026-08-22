check "production_guardrails" {
  assert {
    condition = !var.production_mode || (
      var.enable_org_policies &&
      var.enable_vpc_sc &&
      var.vpc_sc_enforce &&
      var.lock_audit_bucket &&
      var.deletion_protection &&
      length(var.alert_notification_channels) > 0 &&
      trimspace(var.iap_jwt_audience) != ""
    )
    error_message = "production_mode requires Org Policy, enforced VPC-SC, locked audit retention, deletion protection, an alert channel, and an IAP audience."
  }
}
