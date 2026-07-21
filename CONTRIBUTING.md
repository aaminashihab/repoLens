# Contributing to RepoLens

Thank you for your interest in contributing to RepoLens! We welcome all contributions, including bug reports, feature requests, documentation improvements, and pull requests.

## 🤝 Code of Conduct

Please be respectful and constructive in all communication with the maintainers and other contributors.

## 🐛 Reporting Issues

If you find a bug or have a feature request:
1. Search the existing issues to see if it has already been reported.
2. If not, open a new issue. Include as much detail as possible, such as steps to reproduce, expected behavior, logs, and screenshots if applicable.

## 🛠️ Pull Requests

We follow the standard GitHub flow:
1. **Fork** the repository to your own account.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/aaminashihab/repoLens.git
   cd repoLens
   ```
3. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Set up the environment**:
   - Create a virtual environment: `python -m venv .venv`
   - Activate it and install dependencies:
     ```bash
     # On Windows:
     .venv\Scripts\activate
     # On macOS/Linux:
     source .venv/bin/activate

     pip install -r requirements.txt
     ```
5. **Make your changes** and write tests where appropriate.
6. **Run the test suite** to ensure everything works and no regressions are introduced:
   ```bash
   pytest tests/
   ```
7. **Commit your changes** with clear and descriptive commit messages.
8. **Push** your branch to your fork and **open a Pull Request** (PR) to our `main` branch.

## 🧪 Testing Guidelines

- We aim to keep test coverage high. If you are adding a new feature or fixing a bug, please include corresponding unit or integration tests in the `tests/` directory.
- Run tests before submitting a PR: `pytest tests/`.

## 📜 License

By contributing, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).
