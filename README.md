
# New York Air Quality Station Streamlit Dashboard

This application reads the existing MongoDB database created by the
`Air_Quality_Station_Analysis_for_the_New_York_area.ipynb` notebook.

Expected MongoDB structure:

    Database: air_quality
    Collection: stations

Each station document should look approximately like:

    {
        "_id": 384,
        "name": "CCNY",
        "provider": "AirNow",
        "is_monitor": true,
        "pm25_sensor_id": 673,
        "parameters": ["o3", "pm25"],
        "first_utc": ...,
        "last_utc": ...,
        "location": {
            "type": "Point",
            "coordinates": [-73.9481, 40.8197]
        }
    }

## 1. Install Python

Recommended Python: 3.10+

## 2. Install packages

    pip install -r requirements.txt

## 3. Create Streamlit secrets

Create this folder:

    .streamlit

Inside it create:

    secrets.toml

Put:

    MONGO_URI = "mongodb+srv://USERNAME:PASSWORD@YOUR_CLUSTER.mongodb.net/"

Do not upload `secrets.toml` to GitHub.

## 4. Run

From the project folder:

    streamlit run app.py

The browser will open the Streamlit dashboard.

## Dashboard pages

1. Overview
2. Station Explorer
3. Nearest Stations ($near vs $geoNear)
4. Radius Search ($centerSphere / Central Park)
5. Bounding Box Search ($box / Brooklyn)
6. NYC Boroughs (Containment, Spatial Joins & Aggregations)
7. Buffer Overlay & Voronoi Analysis (m2)
8. Active Monitors
9. Station Density Grid
10. Coverage Gaps
11. Nearest-Neighbour Analysis

## Important

The app assumes the MongoDB collection already contains the 72 station
documents created by the Colab notebook.

If the collection is empty, run the data ingestion portion of the notebook
once to populate MongoDB.
