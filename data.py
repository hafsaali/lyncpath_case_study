"""
data.py — Mock tracking payload used as fallback when live APIs are unavailable.
"""

MOCK_TRACKING_PAYLOAD = {
    "source": "mock_fallback",
    "container_number": "MRKU7654321",
    "booking_number": "266782530",
    "shipper": "FALCON TOBACCO",
    "customs_broker": "EASTERN AGENCIES",
    "broker_email": "ops@easternagencies.it",
    "broker_phone": "+39 081 555 0147",
    "milestones": [
        {"id": 1,  "name": "Gate-in at Origin Port",       "status": "complete", "timestamp": "2026-03-07T14:00:00Z", "location": "Port Qasim"},
        {"id": 2,  "name": "Vessel Departed Origin",        "status": "complete", "timestamp": "2026-03-12T06:00:00Z", "location": "Port Qasim"},
        {"id": 3,  "name": "Transshipment",                 "status": "complete", "timestamp": "2026-03-20T11:00:00Z", "location": "Salalah"},
        {"id": 4,  "name": "Transshipment",                 "status": "complete", "timestamp": "2026-04-07T09:00:00Z", "location": "Tanger Med"},
        {"id": 5,  "name": "Vessel Arrived at POD",         "status": "complete", "timestamp": "2026-04-29T06:00:00Z", "location": "Naples"},
        {"id": 6,  "name": "Container Discharged",          "status": "complete", "timestamp": "2026-04-29T14:00:00Z", "location": "Naples"},
        {"id": 7,  "name": "Customs Import Clearance",      "status": "pending",  "timestamp": None,                  "location": ""},
        {"id": 8,  "name": "Gate-out from Terminal",        "status": "pending",  "timestamp": None,                  "location": ""},
        {"id": 9,  "name": "Empty Container Returned",      "status": "pending",  "timestamp": None,                  "location": ""},
    ],
    "lfd": "2026-04-30T23:59:00Z",
    "current_time": "2026-04-29T10:00:00Z",
    "responsibility_map": {
        "customs_clearance":      "customs_broker",
        "demurrage_at_terminal":  "shipper",
        "detention_of_container": "shipper",
    },
    "dnd_rate_card": {
        "carrier": "Maersk",
        "currency": "USD",
        "destination_free_days": 5,
        "demurrage_rate_per_container_per_day": 200,
        "detention_rate_per_container_per_day": 150,
        "container_count": 8,
        "container_type": "40DRY",
    },
    "port_info": {
        "pod_name": "Naples, Italy",
        "terminal": "Naples Terminal Flavio Gioia SpA",
        "terminal_contact": "+39 081 206 3111",
    },
}