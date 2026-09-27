"""Byte-exact freeze of the pre-existing receipts, so later work cannot drift them.

These are regression locks, not correctness proofs: they record the canonical bytes
that `fleet.compare` and `geofleet.experiment` produce at the commit that froze them.
Any intentional engine change must update the constants deliberately, in a commit that
says so, not silently.
"""
import hashlib

import pytest

from almanac.fleet import compare, default_scenario
from almanac.geofleet import AREAS, experiment
from almanac.replay import canonical, digest

# (canonical byte length, sha256 of those bytes, receipt_sha256 carried in the report).
FROZEN_COMPARE = {
    10: (7676932, '6c3a2c25280aa68b3650e2bfea91bedf03d295abd2181ce6b36176dcc5acf7de',
         'd82d64c82396cb8e9388b1e24804811f596182447e7557830629eba4cf0c2b6a'),
    5: (7676931, '6f5cead2c4745ef456433c4464942104249fff4f90e93d3d4a4e8101debe4159',
        '28aaedd9ec64d6aef029b2b1a2c243204fef71e543ca96d775bd8419bb48ebd6'),
}
FROZEN_EXPERIMENT = {
    'all': (14897396, 'dd0f0c34152547944221511e33effcf2db964ab06375e2ffe4b61c6414b58594',
            '38e375daa0dd3281deaceadc0cb5089aae30c2339439de1454fb73b6ef7b6af4'),
    'core': (16093257, '8a5f48f037fbb78887a63bf065cf9110cc348648c3e6bd6faa3704675298a882',
             'e6b5a7dff79d961ae026fd80dc3d2d0d8b1866f9e7b3939c5d5bb65849f24228'),
    'wide': (15427518, '844df2ec62734ec7e6d1bce443d50020abfebd6adc983d9b12b14c1c95336f08',
             'e23d979718c2598d003385d077d97f245719f97ad380b064d4dfca19398a4fc4'),
    'west': (16092206, '5c518fd893adab1c10e62966ac7625f0d45b338cce3d5d883d209b1166fb5e35',
             '2031fec174e8214a2a6f6639c5b2df3f1adebcb3d4c6e5c8600e3007a33c7a6c'),
    'empty': (16466456, '8fd1c813cf08876b40cba2e5e4490aac69328600c1e4afc68877830a5d2886e2',
              '9e735e34d5e303d79ee0d6ef63fcf95f73d1dfc9d64d97e214eac9328e1e57c4'),
}


def frozen(report: dict) -> tuple[int, str, str]:
    body = canonical(report).encode()
    return len(body), hashlib.sha256(body).hexdigest(), report['receipt_sha256']


@pytest.mark.parametrize('step_s', sorted(FROZEN_COMPARE))
def test_legacy_compare_bytes_are_frozen(step_s):
    assert frozen(compare(default_scenario(), step_s)) == FROZEN_COMPARE[step_s]


@pytest.mark.parametrize('area', sorted(FROZEN_EXPERIMENT))
def test_legacy_experiment_bytes_are_frozen(area):
    assert frozen(experiment({'area': area})) == FROZEN_EXPERIMENT[area]


def test_frozen_areas_cover_every_published_area():
    assert set(FROZEN_EXPERIMENT) == set(AREAS)


def test_repeated_compare_calls_produce_identical_bytes():
    assert canonical(compare(default_scenario())) == canonical(compare(default_scenario()))


@pytest.mark.parametrize('build', [lambda: compare(default_scenario()), lambda: experiment({'area': 'west'})])
def test_receipt_hash_covers_the_body_that_ships(build):
    """The stored hash must be the digest of the report minus the hash field itself."""
    report = build()
    stored = report.pop('receipt_sha256')
    assert digest(report) == stored
