FROM lzzy12/mega-sdk-python


# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create necessary directories
RUN mkdir -p downloads

# Run bot
CMD ["python3", "bot.py"] 