from setuptools import setup, find_packages

setup(
    name="girder-virtual-resources",
    version="1.0.0",
    description="Plugin mapping Girder Folder to physical directories.",
    packages=find_packages(),
    include_package_data=True,
    install_requires=["girder"],
    entry_points={
        "girder.plugin": ["virtual_resources = virtual_resources:VirtualResourcesPlugin"]
    },
    zip_safe=False,
)
