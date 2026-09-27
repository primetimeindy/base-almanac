"""Preview results must not hand callers live references to module-global areas."""
import copy
import pytest
from almanac import geofleet


@pytest.fixture
def pristine_areas():
    """Snapshot AREAS and restore it in place, even when an assertion fails."""
    original = copy.deepcopy(geofleet.AREAS)
    try:
        yield copy.deepcopy(original)
    finally:
        geofleet.AREAS.clear()
        geofleet.AREAS.update(copy.deepcopy(original))


def test_separate_previews_do_not_share_areas_or_nested_bounds(pristine_areas):
    first = geofleet.preview({'area': 'core'})
    second = geofleet.preview({'area': 'core'})
    assert first['areas'] is not second['areas']
    assert first['areas']['core'] is not second['areas']['core']
    assert first['areas']['core']['bounds'] is not second['areas']['core']['bounds']
    assert first['bounds'] is not second['bounds']
    second['areas']['core']['bounds'][2] = -97.05
    second['areas']['wide']['label'] = 'mutated by caller'
    second['areas'].pop('west')
    second['bounds'][0] = -179.0
    assert first['areas'] == pristine_areas
    assert first['bounds'] == pristine_areas['core']['bounds']


def test_preview_mutation_protects_global_areas_and_later_selection(pristine_areas):
    first = geofleet.preview({'area': 'core'})
    selected = list(first['selected_ids'])
    assert selected, 'core preset must select devices for this regression to mean anything'
    first['areas']['core']['bounds'][2] = -97.05  # shrink the rectangle to near-empty
    first['areas']['empty']['label'] = 'tampered'
    first['areas']['injected'] = {'label': 'not allowlisted', 'bounds': [-99, 31, -98, 32]}
    first['bounds'][2] = -97.05
    assert geofleet.AREAS == pristine_areas
    later = geofleet.preview({'area': 'core'})
    assert later['bounds'] == pristine_areas['core']['bounds']
    assert later['selected_ids'] == selected
    assert sorted(later['areas']) == sorted(pristine_areas)
    with pytest.raises(ValueError):
        geofleet.preview({'area': 'injected'})


def test_existing_receipt_survives_a_later_unrelated_preview_mutation(pristine_areas):
    report = geofleet.experiment({'area': 'core'})
    receipt = report['receipt_sha256']
    body = dict(report)
    body.pop('receipt_sha256')
    assert geofleet.digest(body) == receipt
    other = geofleet.preview({'area': 'wide'})
    other['areas']['core']['bounds'][0] = -90.0
    other['areas']['core']['label'] = 'tampered'
    other['bounds'][1] = 29.0
    assert report['selection']['areas'] == pristine_areas
    assert report['selection']['bounds'] == pristine_areas['core']['bounds']
    recheck = dict(report)
    assert recheck.pop('receipt_sha256') == receipt
    assert geofleet.digest(recheck) == receipt
