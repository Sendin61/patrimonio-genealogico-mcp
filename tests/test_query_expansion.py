from rob.models import GenealogyQuery
from rob.query_expansion import expand_query


def test_query_expansion_keeps_original_and_unaccented() -> None:
    query = GenealogyQuery(
        name="Manuel Pérez Eiriz",
        places=["Merlán", "Chantada"],
        spouse="Ramona Sindín",
    )
    expanded = expand_query(query)
    assert "Manuel Pérez Eiriz" in expanded
    assert "Manuel Perez Eiriz" in expanded
    assert any("Merlán" in item for item in expanded)
    assert any("Ramona Sindín" in item for item in expanded)
