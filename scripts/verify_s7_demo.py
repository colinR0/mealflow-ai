"""Read credentials from a local file; print only non-secret S7 verification results."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-file", type=Path, required=True)
    args = parser.parse_args()
    credentials = json.loads(args.credential_file.read_text(encoding="utf-8"))
    with httpx.Client(base_url="http://127.0.0.1:8002", timeout=15) as client:
        client.headers["Origin"] = "http://127.0.0.1:5175"
        login = client.post("/api/v2/auth/login", json=credentials)
        assert login.status_code == 200, f"Login HTTP {login.status_code}"
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        households = client.get("/api/v2/households").json()
        household = next(row for row in households if row["name"] == "S7 独立向量演示家庭")
        household_id = household["id"]
        profiles = client.get(f"/api/v2/households/{household_id}/profiles").json()
        assert any(row["restrictions"] == ["花生", "虾"] for row in profiles)
        lots = client.get(f"/api/v2/households/{household_id}/inventory-lots").json()
        assert len(lots) == 4
        response = client.post(
            "/api/v2/agent/turn-runs",
            headers={"Idempotency-Key": "s7-verify-" + uuid4().hex},
            json={"household_id": household_id, "text": "查看库存"},
        )
        assert response.status_code == 202, f"Turn HTTP {response.status_code}"
        run_id = response.json()["run_id"]
        for _ in range(50):
            response = client.get(f"/api/v2/agent/turn-runs/{run_id}")
            assert response.status_code == 200
            result = response.json()
            if result["status"] not in ("queued", "running"):
                break
            time.sleep(0.2)
        assert result["status"] == "succeeded", f"Worker status {result['status']}"
        assert len(result["result"]["evidence"]) == 4
        print(json.dumps({"login": "ok", "permanent_restrictions": "ok", "inventory_lots": 4,
                          "worker_run_id": run_id, "worker_status": result["status"]}))


if __name__ == "__main__":
    main()
