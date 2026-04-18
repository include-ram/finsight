import json, urllib.request

MB = "http://localhost:3000"
SESSION = open("/tmp/mb_session.txt").read().strip()
DB_ID = 2

def api(method, path, data=None):
    url = MB + path
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Metabase-Session", SESSION)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()}

REGEX = "^[0-9]+(\\.[0-9]+)?$"

cards = [
    {
        "name": "Income vs Expenses",
        "query": (
            "SELECT CASE WHEN c.category = 'Income' THEN 'Income' ELSE 'Expense' END AS type, "
            "ROUND(SUM(e.field_value::NUMERIC),2) AS total "
            "FROM extracted_data e "
            "JOIN documents d ON e.document_id = d.id "
            "JOIN categories c ON c.document_id = d.id "
            "WHERE e.field_name = 'total_amount' "
            "AND e.field_value ~ '" + REGEX + "' "
            "AND d.status = 'completed' "
            "GROUP BY type"
        ),
        "display": "bar",
        "viz": {"graph.dimensions": ["type"], "graph.metrics": ["total"]}
    },
    {
        "name": "Spending by Category",
        "query": (
            "SELECT c.category, ROUND(SUM(e.field_value::NUMERIC),2) AS total "
            "FROM extracted_data e "
            "JOIN documents d ON e.document_id = d.id "
            "JOIN categories c ON c.document_id = d.id "
            "WHERE e.field_name = 'total_amount' "
            "AND e.field_value ~ '" + REGEX + "' "
            "AND d.status = 'completed' "
            "AND c.category != 'Income' "
            "GROUP BY c.category ORDER BY total DESC"
        ),
        "display": "pie",
        "viz": {"pie.dimension": "category", "pie.metric": "total"}
    },
    {
        "name": "Monthly Cash Flow",
        "query": (
            "SELECT TO_CHAR(d.upload_date, 'YYYY-MM') AS month, "
            "ROUND(SUM(CASE WHEN c.category = 'Income' THEN e.field_value::NUMERIC ELSE 0 END),2) AS income, "
            "ROUND(SUM(CASE WHEN c.category != 'Income' THEN e.field_value::NUMERIC ELSE 0 END),2) AS expenses "
            "FROM extracted_data e "
            "JOIN documents d ON e.document_id = d.id "
            "JOIN categories c ON c.document_id = d.id "
            "WHERE e.field_name = 'total_amount' "
            "AND e.field_value ~ '" + REGEX + "' "
            "AND d.status = 'completed' "
            "GROUP BY month ORDER BY month"
        ),
        "display": "line",
        "viz": {"graph.dimensions": ["month"], "graph.metrics": ["income", "expenses"]}
    },
    {
        "name": "Recent Documents",
        "query": (
            "SELECT filename, category, upload_date, status "
            "FROM dashboard_overview "
            "ORDER BY upload_date DESC LIMIT 20"
        ),
        "display": "table",
        "viz": {}
    }
]

card_ids = []
for c in cards:
    r = api("POST", "/api/card", {
        "name": c["name"],
        "dataset_query": {
            "type": "native",
            "database": DB_ID,
            "native": {"query": c["query"]}
        },
        "display": c["display"],
        "visualization_settings": c["viz"]
    })
    cid = r.get("id", "ERR: " + str(r.get("error", r))[:100])
    print("Card '{}': {}".format(c["name"], cid))
    card_ids.append(cid)

# Delete existing dashboards
for did in [2, 3]:
    api("DELETE", "/api/dashboard/{}".format(did))

# Create the dashboard
dash = api("POST", "/api/dashboard", {
    "name": "FinSight Overview",
    "description": "Financial document analytics"
})
dash_id = dash.get("id")
print("Dashboard ID: {}".format(dash_id))

# Layout: row, col, width, height
layout = [
    (0,  0,  9, 6),   # Income vs Expenses - top left
    (0,  9,  9, 6),   # Spending by Category - top right
    (6,  0, 18, 6),   # Monthly Cash Flow - full width
    (12, 0, 18, 6),   # Recent Documents - full width
]

for cid, (row, col, sx, sy) in zip(card_ids, layout):
    if isinstance(cid, int):
        r = api("POST", "/api/dashboard/{}/cards".format(dash_id), {
            "cardId": cid, "row": row, "col": col, "size_x": sx, "size_y": sy
        })
        print("  Added card {} at ({},{})".format(cid, row, col))
    else:
        print("  Skipped (error): {}".format(cid))

print("\nDone!")
print("Dashboard: http://52.23.233.3:3000/dashboard/{}".format(dash_id))
print("Login:     admin@finsight.com / Finsight2024!")
