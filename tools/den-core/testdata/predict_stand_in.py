"""A stand-in for tools/den-light/predict.py, for den-core's suite on a branch without den-light: the same call, on
the fake's rule, so the scoring plumbing has something that is not the fake to stand behind. Only a test reaches this
file, by patching light_adapter.PREDICT_PATH. With DEN_LIGHT_DIR set, or on the merged tree, the tests use the real one.
"""
import fake_light


def prepare(model):
    return model


def predict(scene, model, daylight=None):
    return fake_light.predict(scene, model)
