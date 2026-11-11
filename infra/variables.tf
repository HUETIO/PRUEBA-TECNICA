variable "aws_region" {
  description = "AWS region for the not-deployed reference infrastructure."
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name."
  type        = string
  default     = "bia-data-platform-reference-change-me"
}

variable "name_prefix" {
  description = "Prefix used for IAM roles and CloudWatch resources."
  type        = string
  default     = "bia-data-platform"
}
