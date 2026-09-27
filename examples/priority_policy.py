"""A third example: fill fresh devices in ID order, not equal shares.

No physical integration. Do not interpret this simple ordering as optimal.
"""
from fractions import Fraction


class Policy:
    policy_id = 'priority-order'
    policy_version = '1.0.0'

    def decide(self, observation: dict) -> dict:
        """Use only the current bounded observation; allocate deterministically."""
        if observation['schema'] != 'almanac.observation.v1':
            raise ValueError('unsupported observation contract')
        remaining = Fraction(str(observation['dispatch_budget_kw']))
        powers = {}
        for device in observation['devices']:
            power = min(remaining, Fraction(str(device['dispatch_cap_kw'])))
            # Floor to microwatts; never round above remaining budget or cap.
            power = Fraction((power * 10**6).__floor__(), 10**6)
            powers[device['id']] = float(power)
            remaining -= power
        return {'schema': 'almanac.actions.v1', 'powers_kw': powers}
