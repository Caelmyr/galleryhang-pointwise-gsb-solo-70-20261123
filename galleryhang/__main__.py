import os

from waitress import serve

from .web import create_app


if __name__ == '__main__':
    app = create_app(os.environ.get('GALLERYHANG_DATA', '.galleryhang'))
    serve(
        app,
        host=os.environ.get('HOST', '127.0.0.1'),
        port=int(os.environ.get('PORT', '8000')),
        threads=int(os.environ.get('WAITRESS_THREADS', '4')),
        max_request_body_size=64 * 1024 * 1024,
        channel_timeout=60,
        ident='GalleryHang',
    )
