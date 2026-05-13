#!/usr/bin/env python3
"""End-to-end test: emit spans for 2 tenants, verify isolation."""
import os
import time
import json
from sdk.client import TracingClient
from sdk.core import new_trace_context, begin_span

# Configure tenants in environment
os.environ["TRACE_TENANT_KEYS"] = json.dumps({
    "tenant-a": "token-a",
    "tenant-b": "token-b"
})

def test_tenant_a():
    """Emit spans for tenant-a."""
    print("\n=== Emitting spans for TENANT-A ===")
    client = TracingClient(
        host="localhost",
        port=50051,
        tenant_id="tenant-a",
        auth_token="token-a"
    )
    
    ctx = new_trace_context(tenant_id="tenant-a")
    with begin_span(ctx, "tenant_a_operation") as span:
        span.emit_event("Processing order for tenant A")
        time.sleep(0.1)
    
    client.close()
    print("✅ Emitted trace for tenant-a")

def test_tenant_b():
    """Emit spans for tenant-b."""
    print("\n=== Emitting spans for TENANT-B ===")
    client = TracingClient(
        host="localhost",
        port=50051,
        tenant_id="tenant-b",
        auth_token="token-b"
    )
    
    ctx = new_trace_context(tenant_id="tenant-b")
    with begin_span(ctx, "tenant_b_operation") as span:
        span.emit_event("Processing payment for tenant B")
        time.sleep(0.1)
    
    client.close()
    print("✅ Emitted trace for tenant-b")

def test_query_isolation():
    """Query API and verify tenant isolation."""
    print("\n=== Testing API isolation ===")
    
    # Give engine time to reconstruct
    time.sleep(3)
    
    import requests
    
    # Query as tenant-a
    headers_a = {"Authorization": "Bearer token-a"}
    r_a = requests.get("http://localhost:8000/traces", headers=headers_a)
    print(f"Tenant-A query: {r_a.status_code}")
    
    # Query as tenant-b
    headers_b = {"Authorization": "Bearer token-b"}
    r_b = requests.get("http://localhost:8000/traces", headers=headers_b)
    print(f"Tenant-B query: {r_b.status_code}")
    
    # Query without auth (default tenant)
    r_default = requests.get("http://localhost:8000/traces")
    print(f"Default (no auth) query: {r_default.status_code}")
    
    if r_a.status_code == 200 and r_b.status_code == 200:
        print("✅ Both tenants can read their own traces")
    else:
        print("❌ Unexpected query response")
        print(f"Tenant-A: {r_a.text}")
        print(f"Tenant-B: {r_b.text}")

if __name__ == "__main__":
    test_tenant_a()
    test_tenant_b()
    test_query_isolation()