from setuptools import setup

setup(
    name="girder-virtual-resources",
    version="1.0.0",
    description="Plugin mapping Girder Folder to physical directories.",
    packages=["virtual_resources"],
    install_requires=["girder"],
    entry_points={
        "girder.plugin": ["virtual_resources = virtual_resources:VirtualResourcesPlugin"]
    },
)
