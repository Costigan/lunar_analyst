# NRP First-Slice Deployment Notes

Phase C defines the smallest supported NRP deployment shape for Lunar Analyst:

- one backend/runtime `Deployment`
- one PVC mounted as `workspace_root`
- one `Service`
- optional `Ingress`
- one mounted runtime config from `ConfigMap`
- secret injection via environment variables
- exactly one replica and `Recreate` rollout strategy to avoid overlapping SQLite writers

## Apply Order

```bash
kubectl apply -f deploy/nrp/runtime-pvc.yaml
kubectl apply -f deploy/nrp/runtime-configmap.yaml
kubectl create secret generic lunar-analyst-secrets \
  --from-literal=OPENAI_API_KEY="$OPENAI_API_KEY" \
  --from-literal=LUNAR_ANALYST_MCP_TOKEN="$LUNAR_ANALYST_MCP_TOKEN"
kubectl apply -f deploy/nrp/runtime-deployment.yaml
kubectl apply -f deploy/nrp/runtime-service.yaml
```

Apply `deploy/nrp/runtime-ingress.optional.yaml` only if browser access from outside the namespace is required.

## Required Operator Edits

- set the container image in `deploy/nrp/runtime-deployment.yaml`
- adjust `metadata.namespace` or remove it if a different namespace workflow is used
- tune storage class and PVC size in `deploy/nrp/runtime-pvc.yaml`
- set the host/path/TLS details in the optional ingress
- adjust CPU/memory requests and limits once real workload data exists

## Persistent State Contract

The PVC mounted at `/var/lib/lunar-analyst/workspace` is authoritative for:

- scenario directories
- `scenario_catalog.db`
- `.assistant/`

No repo checkout is mounted into the runtime pod.

## Secret Handling

Secrets are consumed only through env refs in the Deployment:

- `OPENAI_API_KEY`
- `LUNAR_ANALYST_MCP_TOKEN`

Do not write provider secrets into the mounted workspace.

## Rollout Constraint

The first NRP slice is intentionally single-writer:

- `replicas: 1`
- `strategy.type: Recreate`

Do not change that to rolling multi-pod behavior until workspace locking and restart semantics are validated on the chosen storage class.
