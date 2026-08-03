```markdown
# Project

Kuza is a comprehensive Python project designed to automate and streamline various tasks through a modular system.

# Stack

- **Languages**: Python 3.x
- **Key Libraries**: 
  - Flask for the web interface
  - Pandas for data manipulation
  - NumPy for numerical operations
  - TensorFlow for machine learning models
  - SQLAlchemy for database interactions

# Structure

- `/assets`: Static files and media.
- `/kuza2` and `/kuzad2`: Contains code generation utilities.
- `/core`: Core functionality modules.
- `/docs`: Documentation files.
- `/gui`: Graphical user interface components.
- `/pipeline`: Workflow automation scripts.
- `/prompts`: Templates for user interactions.
- `/tests`: Unit tests and integration tests.
- `/tools`: Various tools and helpers.
- `/utils`: Utility functions.
- `main.py.before-kuza` and `main.py.before-web`: Original main files before modifications.
- `main.py`: The primary entry point of the application.
- `install.sh`: Installation script for setting up the environment.

# Commands

To run the project:
```bash
python main.py
```

To test the project:
```bash
pytest tests/
```

To build the documentation:
```bash
make -C docs html
```

# Conventions

- **Code Style**: Follow PEP 8 guidelines.
- **Architecture**: Modules should be loosely coupled for easy maintenance and scalability.

- HTTP library: urllib
# Notes

- Ensure all dependencies are installed by running `pip install -r requirements.txt`.
- The installation script (`install.sh`) automates the setup process.
```
