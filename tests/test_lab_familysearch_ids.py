from __future__ import annotations

from rob.lab.familysearch_ids import dgs_image_to_ark, familysearch_identifier_to_ark, parse_fs_image_id


def test_apid_encoder_matches_observed_familysearch_urls() -> None:
    assert familysearch_identifier_to_ark("TH-909-71960-36656-69") == "3:1:3Q9M-CSKB-29ZL-T"
    assert familysearch_identifier_to_ark("TH-909-71960-36651-64") == "3:1:3Q9M-CSKB-29ZL-L"
    assert familysearch_identifier_to_ark("TH-909-71960-36792-76") == "3:1:3Q9M-CSKB-29ZT-D"


def test_dgs_encoder_generates_neighbor_arks() -> None:
    assert dgs_image_to_ark("008159130", 2396) == "3:2:7ZS7-LMSK-T"
    assert dgs_image_to_ark("008159130", 2395) == "3:2:7ZS7-LMSK-R"
    assert parse_fs_image_id("008159130_02396") == ("008159130", 2396)
