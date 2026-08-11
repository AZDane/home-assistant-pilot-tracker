from custom_components.pilot_tracker.arrival import arrival_signals, distance_nm


def test_distance_and_ground_arrival_evidence():
    assert distance_nm(33.4342, -112.0116, 33.4343, -112.0115) < 1
    flight = {
        "latitude": 33.4342, "longitude": -112.0116,
        "airport_destination_latitude": 33.4343,
        "airport_destination_longitude": -112.0115,
        "on_ground": True,
    }
    assert "ground_near_destination" in arrival_signals(flight, None)


def test_lifecycle_event_is_only_one_signal():
    assert arrival_signals({}, {"event_type": "flightradar24_tracked_landed"}) == {"landed_event"}
