location            = "eastus2"
resource_group_name = "rg-actions-runner-fleet-eus2"
name_prefix         = "f5runnerpilot"

admin_ssh_public_key                     = "ssh-ed25519 AAAAreplace-with-an-operator-public-key runner-admin"
runner_bootstrap_uri                     = "https://example.invalid/f5-runner-bootstrap"
runner_bootstrap_sha256                  = "0000000000000000000000000000000000000000000000000000000000000000"
operator_ipv4_cidrs                      = ["203.0.113.0/24"] # Replace with trusted operator or CI egress CIDRs.
socketless_gallery_image_version_id      = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-runner-images-eus2/providers/Microsoft.Compute/galleries/f5RunnerGallery/images/socketless/versions/1.0.0"
container_build_gallery_image_version_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-runner-images-eus2/providers/Microsoft.Compute/galleries/f5RunnerGallery/images/container-build/versions/1.0.0"

socketless_max_instances      = 6
container_build_max_instances = 2

tags = {
  environment = "pilot"
  owner       = "platform-engineering"
}
