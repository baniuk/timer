from setuptools import find_packages, setup

setup(name='wemo-timer',
      version='1.0.0',
      packages=find_packages('src'),
      package_dir={'': 'src'},
      install_requires=[
          'Click', 'APScheduler', 'dynaconf', 'Flask', 'Pint', 'pywemo',
          'tenacity'
      ],
      entry_points='''
        [console_scripts]
        wemo-timer=wemo_timer.timer:start
      ''')
