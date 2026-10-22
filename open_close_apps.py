# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
import os
import json
import subprocess
from difflib import get_close_matches
from pywinauto import Desktop


# --------------------------------------------------
# WINDOWS SPECIAL LOCATIONS
# --------------------------------------------------

SPECIAL_ITEMS = {
    "recycle bin": "shell:RecycleBinFolder",
    "this pc": "shell:MyComputerFolder",
    "my computer": "shell:MyComputerFolder",
    "control panel": "shell:ControlPanelFolder",
    "desktop": "shell:Desktop",
    "downloads": "shell:Downloads",
    "documents": "shell:Personal",
    "pictures": "shell:My Pictures",
    "network": "shell:NetworkPlacesFolder",
}


# --------------------------------------------------
# NORMALIZE COMMAND
# --------------------------------------------------

def normalize(text):

    text = text.lower().strip()

    words = [
        "please ",
        "open ",
        "launch ",
        "start ",
        "run ",
        "close "
    ]

    for word in words:
        if text.startswith(word):
            text = text[len(word):].strip()

    return text


# --------------------------------------------------
# GET ALL INSTALLED WINDOWS APPS
# --------------------------------------------------

def get_apps():

    try:

        command = (
            "Get-StartApps | "
            "Select-Object Name,AppID | "
            "ConvertTo-Json -Compress"
        )

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                command
            ],
            capture_output=True,
            text=True
        )

        if not result.stdout.strip():
            return []

        apps = json.loads(
            result.stdout
        )

        if isinstance(apps, dict):
            apps = [apps]

        return apps

    except Exception as e:

        print("App discovery error:", e)

        return []


# --------------------------------------------------
# FIND APPLICATION
# --------------------------------------------------

def find_app(name):

    name = normalize(name)

    apps = get_apps()

    # Exact match
    for app in apps:

        if app["Name"].lower() == name:
            return app

    # Partial match
    for app in apps:

        if name in app["Name"].lower():
            return app

    # Closest match
    names = {
        app["Name"].lower(): app
        for app in apps
    }

    match = get_close_matches(
        name,
        names.keys(),
        n=1,
        cutoff=0.6
    )

    if match:
        return names[match[0]]

    return None


# --------------------------------------------------
# OPEN SOMETHING
# --------------------------------------------------

def open_item(command):

    name = normalize(command)

    # Special Windows item
    if name in SPECIAL_ITEMS:

        subprocess.Popen(
            [
                "explorer.exe",
                SPECIAL_ITEMS[name]
            ]
        )

        return f"Opened {name}"


    # Exact file/folder path
    path = command.strip().strip('"')

    if os.path.exists(path):

        os.startfile(path)

        return f"Opened {path}"


    # Installed application
    app = find_app(name)

    if app:

        subprocess.Popen(
            [
                "explorer.exe",
                f"shell:AppsFolder\\{app['AppID']}"
            ]
        )

        return f"Opened {app['Name']}"


    return f"Could not find '{name}'"


# --------------------------------------------------
# GET VISIBLE WINDOWS
# --------------------------------------------------

def get_windows():

    result = []

    for window in Desktop(
        backend="uia"
    ).windows():

        try:

            title = window.window_text().strip()

            if title and window.is_visible():

                result.append(
                    window
                )

        except:
            pass

    return result


# --------------------------------------------------
# SHOW VISIBLE APPLICATIONS
# --------------------------------------------------

def show_windows():

    windows = get_windows()

    print("\nVisible Windows:")

    for number, window in enumerate(
        windows,
        start=1
    ):

        print(
            number,
            window.window_text()
        )


# --------------------------------------------------
# CLOSE APPLICATION
# --------------------------------------------------

import psutil
from pywinauto import Desktop


APP_ALIASES = {
    "gitbash": ["git bash", "mingw64", "mintty"],
    "git bash": ["git bash", "mingw64", "mintty"],
    "vscode": ["visual studio code", "code"],
    "vs code": ["visual studio code", "code"],
    "chrome": ["google chrome", "chrome"],
    "brave": ["brave"],
    "notepad": ["notepad"],
    "calculator": ["calculator"],
}


def close_app(command):

    name = normalize(command)

    search_names = APP_ALIASES.get(
        name,
        [name]
    )

    windows = Desktop(
        backend="uia"
    ).windows()

    # ---------------------------------
    # Method 1: Search visible UI titles
    # ---------------------------------

    for window in windows:

        try:

            title = window.window_text().strip()

            if not title:
                continue

            for search in search_names:

                if search in title.lower():

                    window.close()

                    return f"Closed {title}"

        except:
            pass


    # ---------------------------------
    # Method 2: Search running processes
    # ---------------------------------

    for process in psutil.process_iter(
        ["pid", "name"]
    ):

        try:

            process_name = (
                process.info["name"]
                or ""
            ).lower()

            for search in search_names:

                if search in process_name:

                    process.terminate()

                    return (
                        f"Closed "
                        f"{process.info['name']}"
                    )

        except:
            pass


    return f"Could not find '{name}'"
# --------------------------------------------------
# SHOW INSTALLED APPS
# --------------------------------------------------

def show_apps():

    apps = get_apps()

    apps.sort(
        key=lambda x: x["Name"].lower()
    )

    print("\nInstalled Applications:")

    for number, app in enumerate(
        apps,
        start=1
    ):

        print(
            number,
            app["Name"]
        )


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    while True:

        print("\n" + "=" * 45)
        print("WINDOWS JARVIS CONTROLLER")
        print("=" * 45)

        print("1. Open")
        print("2. Close")
        print("3. Visible Windows")
        print("4. Installed Apps")
        print("5. Exit")

        choice = input(
            "\nChoice: "
        ).strip()


        if choice == "1":

            command = input(
                "What do you want to open? "
            )

            print(
                open_item(command)
            )


        elif choice == "2":

            command = input(
                "What do you want to close? "
            )

            print(
                close_app(command)
            )


        elif choice == "3":

            show_windows()


        elif choice == "4":

            show_apps()


        elif choice == "5":

            break


        else:

            print("Invalid choice")


if __name__ == "__main__":
    main()