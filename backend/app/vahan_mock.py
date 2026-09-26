"""
Mock VAHAN & CCTNS Integration Engine for GUIVIN Hackathon Demo
Simulates API responses for vehicle registration, owner details, and stolen vehicle flags.
"""
from typing import Dict, Any

MOCK_VEHICLES = {
    # Typical standard plate
    "GJ01AB1234": {
        "owner": "Ashok Patel",
        "vehicle_class": "LMV (Car)",
        "registration_status": "ACTIVE",
        "insurance_valid": True,
        "puc_valid": True,
        "stolen_flag": False,
        "wanted_flag": False
    },
    # Stolen/Wanted flag triggered
    "GJ05CH9999": {
        "owner": "REDACTED - POLICE HOLD",
        "vehicle_class": "HMV (Truck)",
        "registration_status": "SUSPENDED",
        "insurance_valid": False,
        "puc_valid": False,
        "stolen_flag": True,
        "wanted_flag": True,
        "alert_context": "Vehicle reported stolen in Surat. Suspected use in smuggling."
    },
    # Expired PUC/Insurance
    "GJ21BH1234": {
        "owner": "Nisha Shah",
        "vehicle_class": "Two-Wheeler",
        "registration_status": "ACTIVE",
        "insurance_valid": False,
        "puc_valid": False,
        "stolen_flag": False,
        "wanted_flag": False
    }
}

def lookup_vahan(plate: str) -> Dict[str, Any]:
    """
    Simulate a synchronous lookup to the MoRTH VAHAN database.
    """
    normalized = plate.replace(" ", "").replace("-", "").upper()
    
    # Try to match the mock data, or return a default 'unknown' response for random plates
    result = MOCK_VEHICLES.get(normalized, {
        "owner": "UNKNOWN",
        "vehicle_class": "UNKNOWN",
        "registration_status": "NOT_FOUND",
        "insurance_valid": False,
        "puc_valid": False,
        "stolen_flag": False,
        "wanted_flag": False
    })
    
    return {
        **result,
        "source": "VAHAN-MOCK",
        "note": "Demo mock data; real implementation requires MoRTH production API token."
    }
