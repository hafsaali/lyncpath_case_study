"""
data.py — Mock tracking payload for Agent 2.

Simulates what a project44 / ShipsGo API response would return,
combined with internal milestone state and a D&D rate card.
The LFD and current_time are overridden dynamically by the Streamlit app
based on the selected urgency scenario.
"""

MOCK_TRACKING_PAYLOAD = {
    "source": "project44_simulation",
    "container_number": "MRKU7654321",
    "shipper": "FALCON TOBACCO",
    "customs_broker": "EASTERN AGENCIES",
    "broker_email": "ops@easternagencies.it",
    "broker_phone": "+39 081 555 0147",

    "milestones": [
        {
            "id": 1,
            "name": "Booking Confirmed",
            "status": "complete",
            "timestamp": "2026-02-25T09:41:00Z"
        },
        {
            "id": 2,
            "name": "Container Released at Origin Depot",
            "status": "complete",
            "timestamp": "2026-03-02T07:30:00Z"
        },
        {
            "id": 3,
            "name": "Gate-in at Port Qasim",
            "status": "complete",
            "timestamp": "2026-03-07T14:00:00Z"
        },
        {
            "id": 4,
            "name": "Vessel Departed Origin",
            "status": "complete",
            "timestamp": "2026-03-12T06:00:00Z"
        },
        {
            "id": 5,
            "name": "Transshipment 1 Complete (Salalah)",
            "status": "complete",
            "timestamp": "2026-03-20T11:00:00Z"
        },
        {
            "id": 6,
            "name": "Transshipment 2 Complete (Tanger Med)",
            "status": "complete",
            "timestamp": "2026-04-07T09:00:00Z"
        },
        {
            "id": 7,
            "name": "Transshipment 3 Complete (Algeciras)",
            "status": "complete",
            "timestamp": "2026-04-16T15:00:00Z"
        },
        {
            "id": 8,
            "name": "Vessel Departed Final Transshipment",
            "status": "complete",
            "timestamp": "2026-04-23T08:00:00Z"
        },
        {
            "id": 9,
            "name": "Vessel Arrived at POD (Naples)",
            "status": "complete",
            "timestamp": "2026-04-29T06:00:00Z"
        },
        {
            "id": 10,
            "name": "Customs Import Clearance",
            "status": "pending",
            "timestamp": None
        },
        {
            "id": 11,
            "name": "Gate-out from Terminal",
            "status": "pending",
            "timestamp": None
        }
    ],

    # Overridden by app.py based on scenario
    "lfd": "2026-04-30T23:59:00Z",
    "current_time": "2026-04-29T10:00:00Z",

    "responsibility_map": {
        "customs_clearance": "customs_broker",
        "demurrage_at_terminal": "shipper",
        "detention_of_container": "shipper"
    },

    "dnd_rate_card": {
        "carrier": "Maersk",
        "currency": "USD",
        "destination_free_days": 5,
        "demurrage_rate_per_container_per_day": 200,
        "detention_rate_per_container_per_day": 150,
        "container_count": 8,
        "container_type": "40DRY"
    },

    "port_info": {
        "pod_name": "Naples, Italy",
        "terminal": "Naples Terminal Flavio Gioia SpA",
        "terminal_operating_hours": "06:00–22:00 local",
        "terminal_contact": "+39 081 206 3111"
    }
}
