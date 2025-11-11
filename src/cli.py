import os

import typer

from src.auto_rsa import main as rsa_main

app = typer.Typer()


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def main(ctx: typer.Context, debug: bool = typer.Option(False, "--debug", help="Enable debug logging")) -> None:
    """Entry point for the CLI."""
    if debug:
        os.environ["DEBUG"] = "true"
    rsa_main(ctx.args)


if __name__ == "__main__":
    app()
