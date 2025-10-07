"""
B站催更分析器 - 安装配置文件
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="bilibili-hiatus-analyzer",
    version="1.0.0",
    author="DAILtech",
    author_email="",
    description="自动分析B站关注UP主更新情况，找出最久没更新的'鸽王'",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/DAILtech/bilibili-hiatus-analyzer",
    project_urls={
        "Bug Reports": "https://github.com/DAILtech/bilibili-hiatus-analyzer/issues",
        "Source": "https://github.com/DAILtech/bilibili-hiatus-analyzer",
        "Documentation": "https://github.com/DAILtech/bilibili-hiatus-analyzer#readme",
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: OS Independent",
        "Natural Language :: Chinese (Simplified)",
    ],
    keywords="bilibili, crawler, analysis, video, up主, 催更",
    python_requires=">=3.7",
    install_requires=[
        "requests>=2.31.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "black>=22.0",
            "flake8>=4.0",
        ],
    },
)

