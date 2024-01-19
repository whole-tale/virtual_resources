from setuptools import find_packages, setup

setup(
    name="girder-virtual-resources",
    version="2.0.0",
    description="Plugin mapping Girder Folder to physical directories.",
    packages=find_packages(),
    include_package_data=True,
    license="Apache 2.0",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    python_requires=">=3.10",
    setup_requires=["setuptools-git"],
    install_requires=["girder>=3"],
    entry_points={
        "girder.plugin": [
            "girder_virtual_resources = girder_virtual_resources:VirtualResourcesPlugin"
        ]
    },
    zip_safe=False,
)
