terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "0.11.0"
    }
  }
}
