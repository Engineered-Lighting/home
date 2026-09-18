# The sofa gradient refuses a drawing that does not match the cameras

`build-gradient-lighting.py` divides Frigate box pixels by the camera's detect
size to place a person between the sofa lights. It read that size from
`spatial_model.json` and fell back to 1280x720 when the file did not say.

Nothing reconciled that with what Frigate actually runs. Draw the model at one
size, run the camera at another, and every centroid is scaled by the wrong
factor. The result is still a coordinate between zero and one, so nothing looks
wrong anywhere: the gradient simply lights the wrong bulb. The repository's own
example Frigate config says 960x540 while the model says 1280x720, which is
what raised the question.

Checked against the live instance: every camera runs at 1280x720 and the model
agrees, so the house is correct today. Those true sizes are now recorded in
`house.json`, read from Frigate's own configuration, and the generator refuses
to build when the drawing disagrees with them or when the model declares no
size at all. The guessed default is gone.

The generated package is byte for byte unchanged.
