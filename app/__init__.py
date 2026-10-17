"""Local browser tool for reviewing lung tumour outlines on the practice CTs."""

import sys

# The developer shell exports a Python 3.9 ROS path. Drop it so this
# Python 3.13 environment imports its own packages.
sys.path[:] = [
    path
    for path in sys.path
    if "python3.9" not in path and "anaconda3/envs/ROS" not in path
]
