locals {
  runner_pools = {
    socketless = {
      name         = "socketless"
      vm_size      = "Standard_D8ads_v5"
      minimum      = 0
      maximum      = 30
      os_disk_size = 128
      profile      = "socketless"
    }
    compute = {
      name         = "compute"
      vm_size      = "Standard_D16ads_v5"
      minimum      = 0
      maximum      = 5
      os_disk_size = 128
      profile      = "compute"
    }
    container_build = {
      name         = "build"
      vm_size      = "Standard_D16ads_v5"
      minimum      = 0
      maximum      = 5
      os_disk_size = 128
      profile      = "container-build"
    }
  }

  # 30*8 + 5*16 + 5*16 plus 3*4 system vCPUs. The quota request deliberately
  # targets 600 to retain at least 20% regional and family headroom.
  maximum_runner_vcpus = 30 * 8 + 5 * 16 + 5 * 16
  maximum_system_vcpus = 3 * 4
  required_vcpu_quota  = 600
}

resource "azurerm_resource_group" "runner" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_log_analytics_workspace" "runner" {
  name                = var.log_analytics_workspace_name
  location            = azurerm_resource_group.runner.location
  resource_group_name = azurerm_resource_group.runner.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_kubernetes_cluster" "runner" {
  name                = var.cluster_name
  location            = azurerm_resource_group.runner.location
  resource_group_name = azurerm_resource_group.runner.name
  dns_prefix          = var.cluster_dns_prefix
  kubernetes_version  = var.kubernetes_version
  sku_tier            = "Standard"

  node_os_upgrade_channel   = "NodeImage"
  local_account_disabled    = false
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  auto_scaler_profile {
    scan_interval          = "10s"
    new_pod_scale_up_delay = "0s"
    scale_down_unneeded    = "60m"
  }

  default_node_pool {
    name                         = "system"
    vm_size                      = "Standard_D4as_v5"
    type                         = "VirtualMachineScaleSets"
    auto_scaling_enabled         = true
    node_count                   = 1
    min_count                    = 1
    max_count                    = 3
    only_critical_addons_enabled = true
    max_pods                     = 50
    os_disk_size_gb              = 128
    os_disk_type                 = "Managed"
    os_sku                       = "Ubuntu"
    zones                        = sort(tolist(var.availability_zones))
    node_public_ip_enabled       = false
    node_labels = {
      "runner-profile" = "system"
    }
    upgrade_settings {
      max_surge                     = "33%"
      drain_timeout_in_minutes      = 30
      node_soak_duration_in_minutes = 0
    }
  }

  # Cluster autoscaler owns the live count within the declared 1-3 range.
  lifecycle {
    ignore_changes = [default_node_pool[0].node_count]
  }

  api_server_access_profile {
    authorized_ip_ranges = sort(tolist(var.operator_ipv4_cidrs))
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    load_balancer_sku   = "standard"
    outbound_type       = "loadBalancer"
    pod_cidr            = var.pod_cidr
    service_cidr        = var.service_cidr
    dns_service_ip      = var.dns_service_ip
  }

  oms_agent {
    log_analytics_workspace_id      = azurerm_log_analytics_workspace.runner.id
    msi_auth_for_monitoring_enabled = true
  }

  tags = var.tags
}

resource "azurerm_container_registry" "runner" {
  name                          = "f5salesdemoarcca"
  resource_group_name           = azurerm_resource_group.runner.name
  location                      = azurerm_resource_group.runner.location
  sku                           = "Premium"
  admin_enabled                 = false
  public_network_access_enabled = true
  zone_redundancy_enabled       = true
  anonymous_pull_enabled        = true
  data_endpoint_enabled         = false
  tags                          = var.tags
}

check "quota_headroom" {
  assert {
    condition     = local.required_vcpu_quota >= ceil((local.maximum_runner_vcpus + local.maximum_system_vcpus) / 0.8)
    error_message = "The requested regional and DADSv5 quota must retain at least 20% headroom at maximum fleet capacity."
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "runner" {
  for_each = local.runner_pools

  name                   = each.value.name
  kubernetes_cluster_id  = azurerm_kubernetes_cluster.runner.id
  vm_size                = each.value.vm_size
  mode                   = "User"
  os_type                = "Linux"
  os_sku                 = "Ubuntu"
  priority               = "Regular"
  orchestrator_version   = var.kubernetes_version
  zones                  = sort(tolist(var.availability_zones))
  auto_scaling_enabled   = true
  node_count             = each.value.minimum
  min_count              = each.value.minimum
  max_count              = each.value.maximum
  max_pods               = 50
  scale_down_mode        = "Delete"
  os_disk_type           = "Ephemeral"
  os_disk_size_gb        = each.value.os_disk_size
  node_public_ip_enabled = false

  node_labels = {
    "runner-profile" = each.value.profile
  }

  node_taints = [
    "runner-profile=${each.value.profile}:NoSchedule",
  ]

  upgrade_settings {
    max_surge                     = "33%"
    drain_timeout_in_minutes      = 30
    node_soak_duration_in_minutes = 0
  }

  tags = merge(var.tags, {
    runner-profile = each.value.profile
  })

  lifecycle {
    ignore_changes = [node_count]
  }
}

resource "azurerm_monitor_diagnostic_setting" "control_plane" {
  name                       = "aks-control-plane"
  target_resource_id         = azurerm_kubernetes_cluster.runner.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.runner.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}
