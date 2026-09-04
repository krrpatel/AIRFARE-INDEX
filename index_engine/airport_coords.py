"""
Static airport coordinates (approximate, publicly known locations) for the
India route map. Not scraped/live data -- reference geography only.
"""

AIRPORT_COORDS: dict[str, dict] = {
    "DEL": {"name": "Delhi", "lat": 28.5562, "lon": 77.1000},
    "BOM": {"name": "Mumbai", "lat": 19.0896, "lon": 72.8656},
    "BLR": {"name": "Bengaluru", "lat": 13.1986, "lon": 77.7066},
    "HYD": {"name": "Hyderabad", "lat": 17.2403, "lon": 78.4294},
    "CCU": {"name": "Kolkata", "lat": 22.6547, "lon": 88.4467},
    "MAA": {"name": "Chennai", "lat": 12.9941, "lon": 80.1709},
    "AMD": {"name": "Ahmedabad", "lat": 23.0772, "lon": 72.6347},
    "PNQ": {"name": "Pune", "lat": 18.5822, "lon": 73.9197},
    "JAI": {"name": "Jaipur", "lat": 26.8242, "lon": 75.8122},
    "GOI": {"name": "Goa", "lat": 15.3808, "lon": 73.8314},
    "COK": {"name": "Kochi", "lat": 10.1520, "lon": 76.4019},
    "LKO": {"name": "Lucknow", "lat": 26.7606, "lon": 80.8893},
    "PAT": {"name": "Patna", "lat": 25.5913, "lon": 85.0880},
    "GAU": {"name": "Guwahati", "lat": 26.1061, "lon": 91.5859},
}
