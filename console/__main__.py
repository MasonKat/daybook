"""`python -m console …`: the same CLI as `daybook …` without pip's generated launcher executable.

On Windows always use `.venv\\Scripts\\python.exe -m console <args>`: antivirus software (Norton on the teacher's laptop)
blocks the generated launcher and can kill the terminal. Mac/Linux may keep using `daybook`.
"""
from .cli import main

main()
