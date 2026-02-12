# Development Guide for Customs Invoice Tool

This guide will help you set up and work on the Customs Invoice Tool using Visual Studio Code on your desktop.

## Prerequisites

Before you begin, ensure you have the following installed on your desktop:

### Required Software

1. **Visual Studio Code** (latest version)
   - Download from: https://code.visualstudio.com/

2. **Git** (version 2.0 or higher)
   - Download from: https://git-scm.com/downloads
   - Verify installation: `git --version`

3. **Node.js** (version 18 or higher) and npm
   - Download from: https://nodejs.org/
   - Verify installation: `node --version` and `npm --version`

### Recommended VS Code Extensions

To enhance your development experience, install these extensions in VS Code:

1. **ESLint** (`dbaeumer.vscode-eslint`) - JavaScript/TypeScript linting
2. **Prettier** (`esbenp.prettier-vscode`) - Code formatting
3. **GitLens** (`eamodio.gitlens`) - Enhanced Git capabilities
4. **Live Server** (`ritwickdey.LiveServer`) - Local development server
5. **Path Intellisense** (`christian-kohler.path-intellisense`) - Autocomplete for file paths
6. **JavaScript (ES6) code snippets** (`xabikos.JavaScriptSnippets`) - Useful code snippets

To install extensions:
- Open VS Code
- Press `Ctrl+Shift+X` (Windows/Linux) or `Cmd+Shift+X` (Mac)
- Search for the extension name and click Install

## Getting Started

### 1. Clone the Repository

```bash
# Using HTTPS
git clone https://github.com/stuartpople/customs-invoice-tool.git

# Or using SSH (if you have SSH keys set up)
git clone git@github.com:stuartpople/customs-invoice-tool.git

# Navigate into the project directory
cd customs-invoice-tool
```

### 2. Open in VS Code

```bash
# From the command line
code .

# Or from VS Code:
# File > Open Folder > Select the customs-invoice-tool directory
```

### 3. Install Dependencies

Once you have the project open in VS Code:

```bash
# Install project dependencies
npm install
```

If a `package.json` file doesn't exist yet, you can initialize the project:

```bash
npm init -y
```

## VS Code Configuration

### Workspace Settings

Create a `.vscode/settings.json` file in the project root with recommended settings:

```json
{
  "editor.formatOnSave": true,
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "editor.codeActionsOnSave": {
    "source.fixAll.eslint": "explicit"
  },
  "files.autoSave": "onFocusChange",
  "editor.tabSize": 2,
  "files.exclude": {
    "**/.git": true,
    "**/.DS_Store": true,
    "**/node_modules": true,
    "**/dist": true
  }
}
```

### Recommended Launch Configuration

Create a `.vscode/launch.json` file for debugging:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "type": "node",
      "request": "launch",
      "name": "Launch Program",
      "skipFiles": ["<node_internals>/**"],
      "program": "${workspaceFolder}/index.js"
    },
    {
      "type": "chrome",
      "request": "launch",
      "name": "Launch Chrome",
      "url": "http://localhost:3000",
      "webRoot": "${workspaceFolder}"
    }
  ]
}
```

## Development Workflow

### Running the Application

```bash
# Start the development server
npm start

# Or if using a specific script
npm run dev
```

### Building the Application

```bash
# Build for production
npm run build
```

### Running Tests

```bash
# Run all tests
npm test

# Run tests in watch mode
npm run test:watch

# Run tests with coverage
npm run test:coverage
```

### Linting and Formatting

```bash
# Lint your code
npm run lint

# Fix linting issues automatically
npm run lint:fix

# Format code with Prettier
npm run format
```

## Project Structure

```
customs-invoice-tool/
├── .vscode/              # VS Code configuration
│   ├── settings.json     # Workspace settings
│   └── launch.json       # Debug configurations
├── src/                  # Source code
│   ├── components/       # Reusable components
│   ├── utils/           # Utility functions
│   └── index.js         # Main entry point
├── public/              # Static assets
├── tests/               # Test files
├── .gitignore           # Git ignore rules
├── package.json         # Project dependencies
├── README.md            # Project overview
└── DEVELOPMENT.md       # This file
```

## Useful VS Code Shortcuts

### General
- `Ctrl+P` / `Cmd+P` - Quick file open
- `Ctrl+Shift+P` / `Cmd+Shift+P` - Command palette
- `Ctrl+B` / `Cmd+B` - Toggle sidebar

### Editing
- `Alt+↑/↓` / `Option+↑/↓` - Move line up/down
- `Shift+Alt+↓` / `Shift+Option+↓` - Copy line down
- `Ctrl+/` / `Cmd+/` - Toggle line comment
- `Shift+Alt+F` / `Shift+Option+F` - Format document

### Search
- `Ctrl+F` / `Cmd+F` - Find in file
- `Ctrl+Shift+F` / `Cmd+Shift+F` - Find in files
- `Ctrl+H` / `Cmd+H` - Find and replace

### Debugging
- `F5` - Start debugging
- `F9` - Toggle breakpoint
- `F10` - Step over
- `F11` - Step into

## Git Integration in VS Code

VS Code has excellent built-in Git support:

1. **View Changes**: Click the Source Control icon in the sidebar (`Ctrl+Shift+G`)
2. **Stage Changes**: Click the `+` icon next to changed files
3. **Commit**: Type a commit message and click the checkmark
4. **Push/Pull**: Click the sync icon or use the command palette
5. **Create Branch**: Click the branch name in the status bar

## Terminal Integration

VS Code has an integrated terminal that's very useful:

- Open terminal: `` Ctrl+` `` (backtick) or View > Terminal
- Split terminal: Click the split icon
- Create new terminal: Click the `+` icon
- Switch between terminals: Use the dropdown

## Troubleshooting

### Common Issues

1. **Module not found errors**
   ```bash
   rm -rf node_modules package-lock.json
   npm install
   ```

2. **Port already in use**
   - Change the port in your configuration
   - Or kill the process using the port: `lsof -ti:3000 | xargs kill`

3. **VS Code not recognizing Node.js**
   - Make sure Node.js is in your system PATH
   - Restart VS Code after installing Node.js

4. **ESLint not working**
   - Make sure the ESLint extension is installed
   - Check that ESLint is configured in your project
   - Reload VS Code window: `Ctrl+Shift+P` > "Reload Window"

## Contributing

1. Create a new branch for your feature: `git checkout -b feature/my-feature`
2. Make your changes and commit them: `git commit -m "Add my feature"`
3. Push to your branch: `git push origin feature/my-feature`
4. Open a Pull Request on GitHub

## Additional Resources

- [VS Code Documentation](https://code.visualstudio.com/docs)
- [VS Code Tips and Tricks](https://code.visualstudio.com/docs/getstarted/tips-and-tricks)
- [Node.js Documentation](https://nodejs.org/docs/)
- [Git Documentation](https://git-scm.com/doc)

## Getting Help

If you encounter any issues or have questions:

1. Check the [Issues](https://github.com/stuartpople/customs-invoice-tool/issues) page
2. Create a new issue with a detailed description of your problem
3. Include your environment details (OS, VS Code version, Node.js version)

---

Happy coding! 🎉
