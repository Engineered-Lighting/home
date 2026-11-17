---
title: Account for shared registration uncertainty in Den directions
target: backend
type: fixed
---

Den Live head and torso directions now account for the shared registration translation when propagating relative landmark uncertainty. Invalid residual covariance still withholds the estimate, while measurement uncertainty and the existing geometric quality gates remain enforced.
