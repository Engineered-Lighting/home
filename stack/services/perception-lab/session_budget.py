"""Operator-selected commissioning stages; clients cannot extend a lease."""
import os


def budget(session_seconds=60):
    if type(session_seconds) is not int or session_seconds not in (60, 120, 300):
        raise ValueError('Commissioning stage must be 60, 120 or 300 seconds')
    return {'session_seconds': session_seconds, 'runtime_seconds': session_seconds+60,
            'lease_seconds': session_seconds+55, 'maximum_requests': 2*(session_seconds+60)}


def provisioned_budget():
    value=os.environ.get('PERCEPTION_LAB_SESSION_SECONDS','60')
    if value not in ('60','120','300'):
        raise ValueError('Invalid operator commissioning stage')
    return budget(int(value))


def worker_budget(config):
    duration=config.get('runtime_seconds',120)
    if type(duration) is not int:
        raise ValueError('Explicit bounded runtime required')
    return budget(duration-60)


def verify_process_lifetime(properties, limits):
    seconds=limits['runtime_seconds']
    if (properties.get('RuntimeMaxUSec') not in {str(seconds)+'s',str(seconds//60)+'min'}
            or properties.get('Restart')!='no'):
        raise ValueError('Process lifetime must match provisioned budget without automatic restart')
