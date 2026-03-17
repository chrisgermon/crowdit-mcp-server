"""
GCP Compute Engine tools for Crowd IT MCP Server.
Uses google-cloud-compute library with ADC (automatic on Cloud Run).
Default project: crowdmcp, zone: australia-southeast1-b.
"""

import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "crowdmcp")
GCP_DEFAULT_ZONE = os.environ.get("GCP_DEFAULT_ZONE", "australia-southeast1-b")


def _wait_for_zone_op(operation, project: str, zone: str):
    from google.cloud import compute_v1
    client = compute_v1.ZoneOperationsClient()
    while operation.status != compute_v1.Operation.Status.DONE:
        operation = client.wait(operation=operation.name, zone=zone, project=project)
    if operation.error:
        raise Exception(f"Operation failed: {operation.error}")
    return operation


def register_gcp_compute_tools(mcp):

    @mcp.tool()
    async def gcp_list_instances(zone: Optional[str] = None, project: Optional[str] = None, state_filter: Optional[str] = None) -> str:
        """List GCE instances with name, status, machine type, and IPs.
        Args:
            zone: GCE zone (default: australia-southeast1-b). Use 'all' for all zones.
            project: GCP project ID (default: crowdmcp)
            state_filter: Filter: RUNNING, STOPPED, TERMINATED, or all
        """
        from google.cloud import compute_v1
        project = project or GCP_PROJECT_ID
        zone = zone or GCP_DEFAULT_ZONE
        try:
            client = compute_v1.InstancesClient()
            if zone == "all":
                req = compute_v1.AggregatedListInstancesRequest(project=project)
                instances = []
                for zn, resp in client.aggregated_list(request=req):
                    if resp.instances:
                        for inst in resp.instances:
                            instances.append((zn.replace("zones/", ""), inst))
            else:
                req = compute_v1.ListInstancesRequest(project=project, zone=zone)
                instances = [(zone, inst) for inst in client.list(request=req)]
            if state_filter and state_filter.upper() != "ALL":
                instances = [(z, i) for z, i in instances if i.status == state_filter.upper()]
            results = []
            for iz, inst in instances:
                iip = eip = None
                if inst.network_interfaces:
                    ni = inst.network_interfaces[0]
                    iip = getattr(ni, "network_i_p", None)
                    if ni.access_configs:
                        eip = getattr(ni.access_configs[0], "nat_i_p", None)
                results.append({"name": inst.name, "zone": iz, "status": inst.status, "machine_type": inst.machine_type.split("/")[-1] if inst.machine_type else None, "internal_ip": iip, "external_ip": eip, "labels": dict(inst.labels) if inst.labels else {}, "tags": list(inst.tags.items) if inst.tags and inst.tags.items else [], "created": inst.creation_timestamp})
            return json.dumps({"instances": results, "count": len(results), "project": project})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    async def gcp_get_instance(name: str, zone: Optional[str] = None, project: Optional[str] = None) -> str:
        """Get detailed info about a specific GCE instance.
        Args:
            name: Instance name
            zone: GCE zone (default: australia-southeast1-b)
            project: GCP project ID (default: crowdmcp)
        """
        from google.cloud import compute_v1
        project = project or GCP_PROJECT_ID
        zone = zone or GCP_DEFAULT_ZONE
        try:
            client = compute_v1.InstancesClient()
            inst = client.get(project=project, zone=zone, instance=name)
            disks = [{"name": d.source.split("/")[-1] if d.source else None, "size_gb": d.disk_size_gb, "boot": d.boot} for d in (inst.disks or [])]
            nets = []
            for ni in (inst.network_interfaces or []):
                n = {"network": ni.network.split("/")[-1] if ni.network else None, "internal_ip": getattr(ni, "network_i_p", None), "external_ip": None}
                if ni.access_configs:
                    n["external_ip"] = getattr(ni.access_configs[0], "nat_i_p", None)
                nets.append(n)
            return json.dumps({"name": inst.name, "zone": zone, "status": inst.status, "machine_type": inst.machine_type.split("/")[-1] if inst.machine_type else None, "labels": dict(inst.labels) if inst.labels else {}, "tags": list(inst.tags.items) if inst.tags and inst.tags.items else [], "disks": disks, "networks": nets, "created": inst.creation_timestamp})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    async def gcp_create_instance(name: str, machine_type: str = "e2-standard-4", zone: Optional[str] = None, project: Optional[str] = None, image_family: str = "ubuntu-2404-lts-amd64", image_project: str = "ubuntu-os-cloud", disk_size_gb: int = 100, disk_type: str = "pd-ssd", tags: Optional[str] = None, labels: Optional[str] = None, startup_script: Optional[str] = None, network: str = "default", description: Optional[str] = None) -> str:
        """Create a new GCE instance.
        Args:
            name: Instance name
            machine_type: Machine type (default: e2-standard-4)
            zone: GCE zone (default: australia-southeast1-b)
            project: GCP project ID (default: crowdmcp)
            image_family: OS image family (default: ubuntu-2404-lts-amd64)
            image_project: Image project (default: ubuntu-os-cloud)
            disk_size_gb: Boot disk GB (default: 100)
            disk_type: pd-standard, pd-ssd, pd-balanced (default: pd-ssd)
            tags: Comma-separated network tags
            labels: JSON labels string
            startup_script: Shell script for first boot
            network: VPC network (default: default)
            description: Instance description
        """
        from google.cloud import compute_v1
        project = project or GCP_PROJECT_ID
        zone = zone or GCP_DEFAULT_ZONE
        try:
            client = compute_v1.InstancesClient()
            disk = compute_v1.AttachedDisk(boot=True, auto_delete=True, initialize_params=compute_v1.AttachedDiskInitializeParams(source_image=f"projects/{image_project}/global/images/family/{image_family}", disk_size_gb=disk_size_gb, disk_type=f"zones/{zone}/diskTypes/{disk_type}"))
            ac = compute_v1.AccessConfig(name="External NAT", type_="ONE_TO_ONE_NAT")
            ni = compute_v1.NetworkInterface(network=f"projects/{project}/global/networks/{network}", access_configs=[ac])
            instance = compute_v1.Instance(name=name, machine_type=f"zones/{zone}/machineTypes/{machine_type}", disks=[disk], network_interfaces=[ni], service_accounts=[compute_v1.ServiceAccount(email="default", scopes=["https://www.googleapis.com/auth/cloud-platform"])])
            if description:
                instance.description = description
            if tags:
                instance.tags = compute_v1.Tags(items=[t.strip() for t in tags.split(",")])
            if labels:
                instance.labels = json.loads(labels)
            if startup_script:
                instance.metadata = compute_v1.Metadata(items=[compute_v1.Items(key="startup-script", value=startup_script)])
            op = client.insert(project=project, zone=zone, instance_resource=instance)
            _wait_for_zone_op(op, project, zone)
            created = client.get(project=project, zone=zone, instance=name)
            eip = iip = None
            if created.network_interfaces:
                n0 = created.network_interfaces[0]
                iip = getattr(n0, "network_i_p", None)
                if n0.access_configs:
                    eip = getattr(n0.access_configs[0], "nat_i_p", None)
            return json.dumps({"status": "created", "name": name, "zone": zone, "machine_type": machine_type, "internal_ip": iip, "external_ip": eip})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    async def gcp_instance_action(name: str, action: str, zone: Optional[str] = None, project: Optional[str] = None) -> str:
        """Start, stop, or reset a GCE instance.
        Args:
            name: Instance name
            action: start, stop, reset, suspend, or resume
            zone: GCE zone (default: australia-southeast1-b)
            project: GCP project ID (default: crowdmcp)
        """
        from google.cloud import compute_v1
        project = project or GCP_PROJECT_ID
        zone = zone or GCP_DEFAULT_ZONE
        try:
            client = compute_v1.InstancesClient()
            ops = {"start": client.start, "stop": client.stop, "reset": client.reset, "suspend": client.suspend, "resume": client.resume}
            if action not in ops:
                return json.dumps({"error": f"Unknown action: {action}. Use: start, stop, reset, suspend, resume"})
            op = ops[action](project=project, zone=zone, instance=name)
            _wait_for_zone_op(op, project, zone)
            return json.dumps({"status": "success", "action": action, "instance": name})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    async def gcp_delete_instance(name: str, zone: Optional[str] = None, project: Optional[str] = None) -> str:
        """Delete a GCE instance. Irreversible.
        Args:
            name: Instance name
            zone: GCE zone (default: australia-southeast1-b)
            project: GCP project ID (default: crowdmcp)
        """
        from google.cloud import compute_v1
        project = project or GCP_PROJECT_ID
        zone = zone or GCP_DEFAULT_ZONE
        try:
            client = compute_v1.InstancesClient()
            op = client.delete(project=project, zone=zone, instance=name)
            _wait_for_zone_op(op, project, zone)
            return json.dumps({"status": "deleted", "instance": name})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    async def gcp_get_serial_output(name: str, zone: Optional[str] = None, project: Optional[str] = None) -> str:
        """Get serial port output from a GCE instance (debug startup scripts).
        Args:
            name: Instance name
            zone: GCE zone (default: australia-southeast1-b)
            project: GCP project ID (default: crowdmcp)
        """
        from google.cloud import compute_v1
        project = project or GCP_PROJECT_ID
        zone = zone or GCP_DEFAULT_ZONE
        try:
            client = compute_v1.InstancesClient()
            resp = client.get_serial_port_output(project=project, zone=zone, instance=name)
            content = resp.contents
            if len(content) > 5000:
                content = "...(truncated)...\n" + content[-5000:]
            return json.dumps({"instance": name, "output": content})
        except Exception as e:
            return json.dumps({"error": str(e)})

    logger.info("GCP Compute Engine tools registered (6 tools)")
