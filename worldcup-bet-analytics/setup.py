from setuptools import find_packages, setup


setup(
    name="worldcup-bet-analytics",
    version="0.1.0",
    description="CLI para analisar apostas de futebol por valor esperado usando dados publicos e odds de agregadores.",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[],
    extras_require={"dev": ["pytest>=8.0"]},
    entry_points={
        "console_scripts": [
            "bet365-value-finder=bet365_value_finder.cli:main",
        ]
    },
)
