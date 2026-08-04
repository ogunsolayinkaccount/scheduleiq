from django.urls import path

from .views import (
    analyze,
    metrics,
    narrative,
    quality,
    threshold_profile_detail,
    threshold_profiles,
    upload,
)

urlpatterns = [
    path('upload/', upload, name='upload'),
    path('metrics/', metrics, name='metrics'),
    path('analyze/', analyze, name='analyze'),
    path('quality/', quality, name='quality'),
    path('narrative/', narrative, name='narrative'),
    path('threshold-profiles/', threshold_profiles, name='threshold_profiles'),
    path('threshold-profiles/<str:pk>/', threshold_profile_detail, name='threshold_profile_detail'),
]
