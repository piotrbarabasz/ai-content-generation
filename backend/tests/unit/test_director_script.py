from copy import deepcopy
import pytest
from app.domain.shorts_director import validate_script,script_schema
from tests.integration.test_shorts_director import LLM

@pytest.fixture
def value():
    refs={'verified':{'text':'Cash is an option in a hypothetical outage.'}}
    return LLM().generate_structured('',script_schema(refs,'en',45)),refs

@pytest.mark.parametrize('field,bad',[('language','fr'),('target_duration',90),('source_refs',['foreign']),('source_refs',[{}]),('narration','fabricated text'),('cta','Subscribe'),('hook','')])
def test_invalid_structured_contract(value,field,bad):
    script,refs=value;script=deepcopy(script);script[field]=bad
    with pytest.raises(ValueError):validate_script(script,refs,'en',45)


def test_extra_invented_timestamp_and_unsupported_statistics(value):
    script,refs=value;script['timestamps']=[5]
    with pytest.raises(ValueError):validate_script(script,refs,'en',45)
    del script['timestamps'];script['hook']='999 banks lose contact.';script['beats'][0]['text']=script['hook']
    from app.domain.shorts_director import narration
    script['narration']=narration(script)
    with pytest.raises(ValueError,match='numeric'):validate_script(script,refs,'en',45)
